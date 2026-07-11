"""Adapters and immutable attempts for auditable citation metadata verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from peerassist.citation_metadata import (
    compare_reference_metadata,
    normalize_doi,
    normalize_title,
    title_similarity,
)
from schemas.citation import (
    CitationAdapterInfo,
    CitationMatch,
    CitationSourceRecord,
    CitationVerification,
    RawResponseArtifact,
    ReferenceRecord,
    VerificationStatus,
)

_RETRYABLE_CODES = frozenset({"timeout", "rate_limit", "transient_5xx"})
_REFCHECK_LIST_FIELDS = frozenset({"issues", "error_details", "warning_details", "unverified_details"})
_REFCHECK_INTEGER_FIELDS = frozenset({"total_refs", "errors", "warnings", "unverified"})
_TERMINAL_STATUSES = frozenset(
    {
        VerificationStatus.COMPLETED,
        VerificationStatus.NOT_FOUND,
        VerificationStatus.AMBIGUOUS,
        VerificationStatus.UNAVAILABLE,
    }
)


@dataclass(frozen=True)
class MetadataLookupResult:
    candidates: list[dict[str, Any]]
    raw_response: Any


class CitationAdapterError(Exception):
    """A classified adapter error that is safe for deterministic retry policy."""

    def __init__(self, error_code: str, *, retryable: bool = False, message: str = "") -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.retryable = retryable


class CitationMetadataAdapter(Protocol):
    name: str
    version: str

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        """Return candidate metadata and an auditable raw response."""


class OfflineMetadataVerifier:
    """A network-free, fixed-record adapter for tests and frozen fixtures."""

    name = "offline"
    version = "1"

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._records = [dict(record) for record in records]

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        exact_doi = normalize_doi(record.doi)
        doi_matches = [
            candidate
            for candidate in self._records
            if exact_doi and normalize_doi(candidate.get("doi")) == exact_doi
        ]
        if doi_matches:
            candidates = doi_matches
        elif record.title:
            candidates = [
                candidate
                for candidate in self._records
                if candidate.get("title") and title_similarity(record.title, candidate["title"]) >= 0.85
            ]
        else:
            candidates = []
        candidates = sorted(candidates, key=_candidate_sort_key)
        return MetadataLookupResult(candidates=candidates, raw_response={"records": candidates})


class ExistingRefcheckAdapter:
    """Conservatively map a completed FactReview ``reference_check.json`` payload."""

    name = "existing_refcheck"
    version = "1"

    def __init__(self, payload: Mapping[str, Any] | str | Path) -> None:
        self._payload_source = payload

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        payload = self._load_payload()
        if not _is_refcheck_payload(payload):
            raise CitationAdapterError("adapter_schema_error")
        issues = payload.get("issues")
        if not payload["ok"]:
            raise CitationAdapterError("adapter_unavailable", message=str(payload.get("error_message") or ""))

        candidates: list[dict[str, Any]] = []
        for issue in issues:
            if not self._matches_reference(issue, record):
                continue
            metadata = issue.get("external_metadata")
            if not isinstance(metadata, Mapping):
                continue
            candidate = dict(metadata)
            if issue.get("verified_url") and not candidate.get("url"):
                candidate["url"] = str(issue["verified_url"])
            candidates.append(candidate)
        return MetadataLookupResult(candidates=sorted(candidates, key=_candidate_sort_key), raw_response=dict(payload))

    def _load_payload(self) -> Any:
        if isinstance(self._payload_source, Mapping):
            return dict(self._payload_source)
        try:
            return json.loads(Path(self._payload_source).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CitationAdapterError("adapter_schema_error") from exc

    @staticmethod
    def _matches_reference(issue: Mapping[str, Any], record: ReferenceRecord) -> bool:
        issue_title = normalize_title(issue.get("reference_title"))
        if record.title and issue_title and issue_title != normalize_title(record.title):
            return False
        issue_year = str(issue.get("reference_year") or "").strip()
        return not (record.year is not None and issue_year and issue_year != str(record.year))


def normalize_query(record: ReferenceRecord) -> dict[str, Any]:
    """Build the canonical lookup input used by verification IDs."""
    query: dict[str, Any] = {}
    if normalize_doi(record.doi):
        query["doi"] = normalize_doi(record.doi)
    if normalize_title(record.title):
        query["title"] = normalize_title(record.title)
    if record.year is not None:
        query["year"] = record.year
    return query


def _is_refcheck_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("ok"), bool):
        return False
    if not all(isinstance(payload.get(field), int) and not isinstance(payload[field], bool) for field in _REFCHECK_INTEGER_FIELDS):
        return False
    if not all(isinstance(payload.get(field), list) for field in _REFCHECK_LIST_FIELDS):
        return False
    if not all(isinstance(issue, Mapping) for issue in payload["issues"]):
        return False
    return isinstance(payload.get("error_message"), str) and isinstance(payload.get("report_file"), str)


def verification_id(record: ReferenceRecord, adapter_name: str, adapter_version: str) -> str:
    query_bytes = json.dumps(normalize_query(record), sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(query_bytes).hexdigest()[:8]
    return f"V-{record.id}-{adapter_name}-{adapter_version}-{digest}"


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        normalize_doi(candidate.get("doi")),
        normalize_title(candidate.get("title")),
        str(candidate.get("id") or ""),
    )


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    candidate_id = str(candidate.get("id") or "").strip()
    if candidate_id:
        return candidate_id
    material = json.dumps(dict(candidate), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return f"candidate-{hashlib.sha256(material).hexdigest()[:12]}"


def _write_artifact(artifact_dir: Path, attempt_id: str, payload: Any) -> RawResponseArtifact:
    relative_path = Path("citation_verifications") / f"attempt-{attempt_id}.json"
    destination = artifact_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    if temporary.exists():
        raise FileExistsError(temporary)
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    return RawResponseArtifact(path=relative_path.as_posix(), sha256=hashlib.sha256(encoded).hexdigest())


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _build_verification(
    *,
    record: ReferenceRecord,
    adapter_name: str,
    adapter_version: str,
    status: VerificationStatus,
    attempt_id: str,
    attempt_number: int,
    match: CitationMatch | None = None,
    source_record: CitationSourceRecord | None = None,
    raw_response_artifact: RawResponseArtifact | None = None,
    error_code: str = "",
    observed_metadata: dict[str, Any] | None = None,
    field_differences: list[Any] | None = None,
) -> CitationVerification:
    return CitationVerification(
        id=verification_id(record, adapter_name, adapter_version),
        reference_record_id=record.id,
        source=adapter_name,
        status=status,
        adapter=CitationAdapterInfo(name=adapter_name, version=adapter_version),
        query=normalize_query(record),
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        checked_at=_timestamp(),
        tool_call_id=f"citation-verification-{attempt_id}",
        match=match,
        source_record=source_record,
        raw_response_artifact=raw_response_artifact,
        error_code=error_code,
        observed_metadata=observed_metadata or {},
        field_differences=field_differences or [],
    )


def _unavailable_verification(record: ReferenceRecord, adapter: CitationMetadataAdapter | None, attempt_number: int) -> CitationVerification:
    adapter_name = adapter.name if adapter is not None else "unavailable"
    adapter_version = adapter.version if adapter is not None else "1"
    error_code = "input_unavailable" if not normalize_query(record) else "adapter_unavailable"
    return _build_verification(
        record=record,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        status=VerificationStatus.UNAVAILABLE,
        attempt_id=f"unavailable-{uuid4().hex}",
        attempt_number=attempt_number,
        error_code=error_code,
    )


def verify_reference(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter | None,
    *,
    artifact_dir: str | Path,
    attempt_number: int = 1,
    attempt_id: str | None = None,
) -> CitationVerification:
    """Perform one attempt, preserving an artifact for every received response or error."""
    if adapter is None or not normalize_query(record):
        return _unavailable_verification(record, adapter, attempt_number)

    attempt_id = attempt_id or uuid4().hex
    artifact_root = Path(artifact_dir)
    try:
        result = adapter.lookup(record)
        if not isinstance(result, MetadataLookupResult) or not isinstance(result.candidates, list):
            raise CitationAdapterError("adapter_schema_error")
        if not all(isinstance(candidate, Mapping) for candidate in result.candidates):
            raise CitationAdapterError("adapter_schema_error")
        artifact = _write_artifact(artifact_root, attempt_id, result.raw_response)
    except CitationAdapterError as exc:
        artifact = _write_artifact(
            artifact_root,
            attempt_id,
            {"error_code": exc.error_code, "message": str(exc)},
        )
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.FAILED,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            error_code=exc.error_code,
        )
    except Exception as exc:  # Adapters must not leak unclassified failures into the audit run.
        artifact = _write_artifact(
            artifact_root,
            attempt_id,
            {"error_code": "adapter_error", "message": str(exc)},
        )
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.FAILED,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            error_code="adapter_error",
        )

    candidates = [dict(candidate) for candidate in result.candidates]
    if not candidates:
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.NOT_FOUND,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(
                method="no_candidates", candidate_count=0, selected_candidate_id=None, selection_reason="", candidate_ids=[]
            ),
        )
    candidate_ids = [_candidate_id(candidate) for candidate in candidates]
    if len(candidates) > 1:
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.AMBIGUOUS,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(
                method="multiple_candidates",
                candidate_count=len(candidates),
                selected_candidate_id=None,
                selection_reason="multiple candidates returned by adapter",
                candidate_ids=candidate_ids,
            ),
        )

    candidate = candidates[0]
    exact_doi = bool(record.doi and candidate.get("doi") and normalize_doi(record.doi) == normalize_doi(candidate["doi"]))
    score = title_similarity(record.title, candidate.get("title")) if record.title and candidate.get("title") else 0.0
    if not exact_doi and score < 0.85:
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.NOT_FOUND,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(
                method="title_similarity_no_match", candidate_count=0, selected_candidate_id=None, selection_reason="", candidate_ids=[]
            ),
        )
    if not exact_doi and score < 0.95:
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.AMBIGUOUS,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(
                method="title_similarity_ambiguous",
                candidate_count=1,
                selected_candidate_id=None,
                selection_reason=f"title similarity {score:.4f} is below unique threshold",
                candidate_ids=candidate_ids,
            ),
        )

    candidate_id = candidate_ids[0]
    candidate["id"] = candidate_id
    return _build_verification(
        record=record,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        status=VerificationStatus.COMPLETED,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        raw_response_artifact=artifact,
        match=CitationMatch(
            method="doi_exact" if exact_doi else "title_similarity_unique",
            candidate_count=1,
            selected_candidate_id=candidate_id,
            selection_reason="normalized DOI exact match" if exact_doi else f"title similarity {score:.4f}",
            candidate_ids=candidate_ids,
        ),
        source_record=CitationSourceRecord(id=candidate_id, url=str(candidate.get("url") or "")),
        observed_metadata={key: value for key, value in candidate.items() if key != "id"},
        field_differences=compare_reference_metadata(record, candidate),
    )


def _valid_completed_artifact(verification: CitationVerification, artifact_dir: Path) -> bool:
    artifact = verification.raw_response_artifact
    if artifact is None:
        return False
    path = artifact_dir / artifact.path
    try:
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
    except OSError:
        return False


def select_authoritative_verification(
    verifications: Sequence[CitationVerification], *, artifact_dir: str | Path | None = None
) -> CitationVerification | None:
    """Choose the newest valid terminal, falling back to the newest failure only."""
    valid_terminals = [verification for verification in verifications if verification.status in _TERMINAL_STATUSES]
    if artifact_dir is not None:
        root = Path(artifact_dir)
        valid_terminals = [
            verification
            for verification in valid_terminals
            if verification.status is VerificationStatus.UNAVAILABLE or _valid_completed_artifact(verification, root)
        ]
    if valid_terminals:
        return max(valid_terminals, key=lambda verification: verification.attempt_number)
    failed = [verification for verification in verifications if verification.status is VerificationStatus.FAILED]
    return max(failed, key=lambda verification: verification.attempt_number) if failed else None


def verify_reference_with_retries(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter | None,
    *,
    artifact_dir: str | Path,
    attempts: MutableSequence[CitationVerification] | None = None,
    existing_verifications: Sequence[CitationVerification] | None = None,
    max_attempts: int = 3,
) -> CitationVerification:
    """Retry classified transient failures while preserving every attempt in ``attempts``."""
    all_attempts = attempts if attempts is not None else []
    if existing_verifications:
        all_attempts.extend(existing_verifications)
    if adapter is None or not normalize_query(record):
        verification = _unavailable_verification(record, adapter, max((a.attempt_number for a in all_attempts), default=0) + 1)
        all_attempts.append(verification)
        return verification

    expected_id = verification_id(record, adapter.name, adapter.version)
    for existing in all_attempts:
        if (
            existing.id == expected_id
            and existing.status is VerificationStatus.COMPLETED
            and _valid_completed_artifact(existing, Path(artifact_dir))
        ):
            return existing

    next_attempt = max((attempt.attempt_number for attempt in all_attempts if attempt.id == expected_id), default=0) + 1
    last: CitationVerification | None = None
    for _ in range(max_attempts):
        last = verify_reference(record, adapter, artifact_dir=artifact_dir, attempt_number=next_attempt)
        all_attempts.append(last)
        if last.status is not VerificationStatus.FAILED or last.error_code not in _RETRYABLE_CODES:
            return last
        next_attempt += 1
    return last
