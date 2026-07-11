"""Adapters and attempts for auditable citation metadata verification."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from collections.abc import Mapping, MutableSequence, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from peerassist.citation_artifacts import (
    ArtifactSerializationError,
    ArtifactWriteError,
    validate_response_artifact,
    write_response_artifact,
)
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
_NO_RESPONSE = object()
_REFCHECK_LIST_FIELDS = frozenset({"issues", "error_details", "warning_details", "unverified_details"})
_REFCHECK_INTEGER_FIELDS = frozenset({"total_refs", "errors", "warnings", "unverified"})
_REFCHECK_ROW_FIELDS = frozenset(
    {
        "severity",
        "type",
        "reference_title",
        "reference_year",
        "cited_url",
        "verified_url",
        "details",
        "raw_reference",
        "corrected_bibtex",
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        VerificationStatus.COMPLETED,
        VerificationStatus.NOT_FOUND,
        VerificationStatus.AMBIGUOUS,
        VerificationStatus.UNAVAILABLE,
    }
)
_BIBTEX_FIELD_RE = re.compile(
    r"\b(?P<field>title|year|doi|url)\s*=\s*(?P<value>\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\")",
    re.IGNORECASE,
)
_ATTEMPT_LOCKS: dict[str, threading.Lock] = {}
_ATTEMPT_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class MetadataLookupResult:
    candidates: list[dict[str, Any]]
    raw_response: Any


class CitationAdapterError(Exception):
    """A classified adapter failure, optionally retaining a received response."""

    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool = False,
        message: str = "",
        raw_response: Any = _NO_RESPONSE,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.retryable = retryable
        self.raw_response = raw_response


class CitationMetadataAdapter(Protocol):
    name: str
    version: str

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        """Return candidates and their raw source response, or raise a classified error."""


class OfflineMetadataVerifier:
    """A fixed-record, network-free adapter for tests and frozen fixtures."""

    name = "offline"
    version = "1"

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._records = [dict(record) for record in records]

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        del record
        records = [dict(candidate) for candidate in self._records]
        return MetadataLookupResult(candidates=records, raw_response={"records": records})


class ExistingRefcheckAdapter:
    """Map lossy FactReview ``reference_check.json`` rows without inventing metadata."""

    name = "existing_refcheck"
    version = "1"

    def __init__(self, payload: Mapping[str, Any] | str | Path) -> None:
        self._payload_source = payload

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        payload, raw_response = self._load_payload()
        if not _is_refcheck_payload(payload):
            raise CitationAdapterError("adapter_schema_error", raw_response=raw_response)
        if not payload["ok"]:
            raise CitationAdapterError(
                "source_error",
                message=payload["error_message"],
                raw_response=raw_response,
            )

        matched_rows = _matching_refcheck_rows(payload["issues"], record)
        if matched_rows is None:
            raise CitationAdapterError("adapter_unavailable")
        if all(_is_explicit_no_match(row) for row in matched_rows):
            return MetadataLookupResult(candidates=[], raw_response=raw_response)

        candidates = [_candidate_from_warning(row) for row in matched_rows]
        parsed_candidates = [candidate for candidate in candidates if candidate is not None]
        if not parsed_candidates:
            raise CitationAdapterError("adapter_unavailable")
        return MetadataLookupResult(candidates=parsed_candidates, raw_response=raw_response)

    def _load_payload(self) -> tuple[Any, Any]:
        if isinstance(self._payload_source, Mapping):
            payload = dict(self._payload_source)
            return payload, payload
        source = Path(self._payload_source)
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise CitationAdapterError("adapter_unavailable") from exc
        try:
            return json.loads(raw.decode("utf-8")), raw
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CitationAdapterError("adapter_schema_error", raw_response=raw) from exc


def _is_refcheck_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
        return False
    if not all(isinstance(payload.get(field), int) and not isinstance(payload[field], bool) for field in _REFCHECK_INTEGER_FIELDS):
        return False
    if not all(isinstance(payload.get(field), list) for field in _REFCHECK_LIST_FIELDS):
        return False
    if not isinstance(payload.get("error_message"), str) or not isinstance(payload.get("report_file"), str):
        return False
    return all(
        isinstance(row, dict)
        and _REFCHECK_ROW_FIELDS.issubset(row)
        and all(isinstance(row[field], str) for field in _REFCHECK_ROW_FIELDS)
        for row in payload["issues"]
    )


def _normalized_raw_reference(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _matching_refcheck_rows(rows: list[Any], record: ReferenceRecord) -> list[dict[str, str]] | None:
    typed_rows = [row for row in rows if isinstance(row, dict)]
    raw_matches = [
        row
        for row in typed_rows
        if _normalized_raw_reference(row["raw_reference"]) == _normalized_raw_reference(record.raw_text)
    ]
    if raw_matches:
        return raw_matches
    title_year_matches = [
        row
        for row in typed_rows
        if record.title
        and record.year is not None
        and normalize_title(row["reference_title"]) == normalize_title(record.title)
        and row["reference_year"].strip() == str(record.year)
    ]
    return title_year_matches if len(title_year_matches) == 1 else None


def _is_explicit_no_match(row: Mapping[str, str]) -> bool:
    issue_type = row["type"].casefold()
    if issue_type == "unverified::no_match":
        return True
    return row["severity"].casefold() == "error" and "no_match" in issue_type and (
        "hallucination" in issue_type or "fake" in issue_type
    )


def _candidate_from_warning(row: Mapping[str, str]) -> dict[str, Any] | None:
    if row["severity"].casefold() != "warning" or not row["corrected_bibtex"].strip():
        return None
    fields = _parse_bibtex_fields(row["corrected_bibtex"])
    candidate: dict[str, Any] = {
        "id": f"refcheck-{hashlib.sha256(_canonical_json_bytes(row)).hexdigest()[:12]}"
    }
    for key in ("title", "doi"):
        if fields.get(key):
            candidate[key] = fields[key]
    if fields.get("year"):
        try:
            candidate["year"] = int(fields["year"])
        except ValueError:
            pass
    if row["verified_url"].strip():
        candidate["url"] = row["verified_url"].strip()
    elif fields.get("url"):
        candidate["url"] = fields["url"]
    return candidate if len(candidate) > 1 else None


def _parse_bibtex_fields(bibtex: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _BIBTEX_FIELD_RE.finditer(bibtex):
        value = match.group("value").strip()
        if (value.startswith("{") and value.endswith("}")) or (value.startswith('"') and value.endswith('"')):
            value = value[1:-1].strip()
        fields[match.group("field").casefold()] = value
    return fields


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


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def verification_id(record: ReferenceRecord, adapter_name: str, adapter_version: str) -> str:
    digest = hashlib.sha256(_canonical_json_bytes(normalize_query(record))).hexdigest()[:8]
    return f"V-{record.id}-{adapter_name}-{adapter_version}-{digest}"


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    candidate_id = str(candidate.get("id") or "").strip()
    if candidate_id:
        return candidate_id
    return f"candidate-{hashlib.sha256(_canonical_json_bytes(dict(candidate))).hexdigest()[:12]}"


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


def _unavailable_verification(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter | None,
    attempt_number: int,
    *,
    attempt_id: str | None = None,
) -> CitationVerification:
    return _build_verification(
        record=record,
        adapter_name=adapter.name if adapter is not None else "unavailable",
        adapter_version=adapter.version if adapter is not None else "1",
        status=VerificationStatus.UNAVAILABLE,
        attempt_id=attempt_id or f"unavailable-{uuid4().hex}",
        attempt_number=attempt_number,
        error_code="adapter_unavailable",
    )


def _failed_verification(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter,
    attempt_id: str,
    attempt_number: int,
    error_code: str,
    artifact: RawResponseArtifact | None = None,
) -> CitationVerification:
    return _build_verification(
        record=record,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        status=VerificationStatus.FAILED,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        raw_response_artifact=artifact,
        error_code=error_code,
    )


def _persist_response(
    artifact_dir: str | Path,
    attempt_id: str,
    raw_response: Any,
) -> tuple[RawResponseArtifact | None, str | None]:
    try:
        return write_response_artifact(artifact_dir, attempt_id, raw_response), None
    except ArtifactSerializationError:
        return None, "artifact_serialization_error"
    except (ArtifactWriteError, FileExistsError, ValueError):
        return None, "artifact_write_error"


def verify_reference(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter | None,
    *,
    artifact_dir: str | Path,
    attempt_number: int = 1,
    attempt_id: str | None = None,
) -> CitationVerification:
    """Perform one attempt and preserve every received response exactly once."""
    if adapter is None or not normalize_query(record):
        return _unavailable_verification(record, adapter, attempt_number)

    attempt_id = attempt_id or uuid4().hex
    try:
        result = adapter.lookup(record)
    except CitationAdapterError as exc:
        if exc.error_code == "adapter_unavailable":
            return _unavailable_verification(record, adapter, attempt_number, attempt_id=attempt_id)
        artifact, persistence_error = (
            _persist_response(artifact_dir, attempt_id, exc.raw_response)
            if exc.raw_response is not _NO_RESPONSE
            else (None, None)
        )
        return _failed_verification(
            record,
            adapter,
            attempt_id,
            attempt_number,
            persistence_error or exc.error_code,
            artifact,
        )
    except Exception:
        return _failed_verification(record, adapter, attempt_id, attempt_number, "adapter_error")

    if not isinstance(result, MetadataLookupResult):
        return _failed_verification(record, adapter, attempt_id, attempt_number, "adapter_schema_error")
    artifact, persistence_error = _persist_response(artifact_dir, attempt_id, result.raw_response)
    if persistence_error:
        return _failed_verification(record, adapter, attempt_id, attempt_number, persistence_error)
    if not isinstance(result.candidates, list) or not all(isinstance(candidate, Mapping) for candidate in result.candidates):
        return _failed_verification(record, adapter, attempt_id, attempt_number, "adapter_schema_error", artifact)

    candidates = [dict(candidate) for candidate in result.candidates]
    selected, method, matching_candidates = _select_candidate(record, candidates)
    candidate_ids = [_candidate_id(candidate) for candidate in matching_candidates]
    if method == "no_candidates":
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.NOT_FOUND,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(method=method, candidate_count=0, selected_candidate_id=None, selection_reason="", candidate_ids=[]),
        )
    if selected is None:
        return _build_verification(
            record=record,
            adapter_name=adapter.name,
            adapter_version=adapter.version,
            status=VerificationStatus.AMBIGUOUS,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            raw_response_artifact=artifact,
            match=CitationMatch(
                method=method,
                candidate_count=len(matching_candidates),
                selected_candidate_id=None,
                selection_reason="candidate identity is not unique",
                candidate_ids=candidate_ids,
            ),
        )

    selected_id = _candidate_id(selected)
    observed = dict(selected)
    observed.pop("id", None)
    return _build_verification(
        record=record,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        status=VerificationStatus.COMPLETED,
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        raw_response_artifact=artifact,
        match=CitationMatch(
            method=method,
            candidate_count=1,
            selected_candidate_id=selected_id,
            selection_reason="normalized DOI exact match" if method == "doi_exact" else "unique title similarity match",
            candidate_ids=[selected_id],
        ),
        source_record=CitationSourceRecord(id=selected_id, url=str(selected.get("url") or "")),
        observed_metadata=observed,
        field_differences=compare_reference_metadata(record, selected),
    )


def _select_candidate(
    record: ReferenceRecord, candidates: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    exact_doi = [
        candidate
        for candidate in candidates
        if record.doi and candidate.get("doi") and normalize_doi(record.doi) == normalize_doi(candidate["doi"])
    ]
    if len(exact_doi) == 1:
        return exact_doi[0], "doi_exact", exact_doi
    if len(exact_doi) > 1:
        return None, "multiple_doi_exact", exact_doi

    scored = [
        (candidate, title_similarity(record.title, candidate.get("title")))
        for candidate in candidates
        if record.title and candidate.get("title")
    ]
    unique_matches = [candidate for candidate, score in scored if score >= 0.95]
    if len(unique_matches) == 1:
        return unique_matches[0], "title_similarity_unique", unique_matches
    if len(unique_matches) > 1:
        return None, "title_similarity_ambiguous", unique_matches
    ambiguous_matches = [candidate for candidate, score in scored if score >= 0.85]
    if ambiguous_matches:
        return None, "title_similarity_ambiguous", ambiguous_matches
    return None, "no_candidates", []


def _artifact_valid(verification: CitationVerification, artifact_dir: str | Path) -> bool:
    if verification.raw_response_artifact is None:
        return verification.status in {VerificationStatus.FAILED, VerificationStatus.UNAVAILABLE}
    return validate_response_artifact(artifact_dir, verification.raw_response_artifact)


def select_authoritative_verification(
    verifications: Sequence[CitationVerification], *, artifact_dir: str | Path | None = None
) -> CitationVerification | None:
    """Choose the latest hash-valid terminal, otherwise the latest valid failure."""
    candidates = list(verifications)
    if artifact_dir is not None:
        candidates = [verification for verification in candidates if _artifact_valid(verification, artifact_dir)]
    terminals = [verification for verification in candidates if verification.status in _TERMINAL_STATUSES]
    if terminals:
        return max(terminals, key=lambda verification: verification.attempt_number)
    failed = [verification for verification in candidates if verification.status is VerificationStatus.FAILED]
    return max(failed, key=lambda verification: verification.attempt_number) if failed else None


def _attempt_lock(verification_key: str) -> threading.Lock:
    with _ATTEMPT_LOCKS_GUARD:
        return _ATTEMPT_LOCKS.setdefault(verification_key, threading.Lock())


def verify_reference_with_retries(
    record: ReferenceRecord,
    adapter: CitationMetadataAdapter | None,
    *,
    artifact_dir: str | Path,
    attempts: MutableSequence[CitationVerification] | None = None,
    existing_verifications: Sequence[CitationVerification] | None = None,
    max_attempts: int = 3,
) -> CitationVerification:
    """Run at most ``max_attempts`` serialized attempts for one idempotency key."""
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    adapter_name = adapter.name if adapter is not None else "unavailable"
    adapter_version = adapter.version if adapter is not None else "1"
    verification_key = verification_id(record, adapter_name, adapter_version)
    all_attempts = attempts if attempts is not None else []

    with _attempt_lock(verification_key):
        if existing_verifications:
            all_attempts.extend(existing_verifications)
        retained = [attempt for attempt in all_attempts if attempt.id == verification_key]
        authoritative = select_authoritative_verification(retained, artifact_dir=artifact_dir)
        if authoritative is not None and authoritative.status in _TERMINAL_STATUSES:
            return authoritative

        next_attempt = max((attempt.attempt_number for attempt in retained), default=0) + 1
        if next_attempt > max_attempts:
            selected = select_authoritative_verification(retained, artifact_dir=artifact_dir)
            if selected is not None:
                return selected
            return _failed_verification(
                record,
                adapter if adapter is not None else _UnavailableAdapter(),
                f"invalid-cache-{uuid4().hex}",
                max_attempts,
                "artifact_invalid",
            )

        if adapter is None or not normalize_query(record):
            unavailable = _unavailable_verification(record, adapter, next_attempt)
            all_attempts.append(unavailable)
            return unavailable

        last: CitationVerification | None = None
        for attempt_number in range(next_attempt, max_attempts + 1):
            last = verify_reference(record, adapter, artifact_dir=artifact_dir, attempt_number=attempt_number)
            all_attempts.append(last)
            if last.status is not VerificationStatus.FAILED or last.error_code not in _RETRYABLE_CODES:
                return last
        if last is None:
            raise RuntimeError("attempt allocation unexpectedly produced no verification")
        return last


class _UnavailableAdapter:
    name = "unavailable"
    version = "1"

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        del record
        raise CitationAdapterError("adapter_unavailable")
