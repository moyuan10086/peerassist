from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from peerassist.citation_metadata import (
    compare_reference_metadata,
    normalize_doi,
    normalize_title,
    title_similarity,
)
from peerassist.citation_verification import (
    CitationAdapterError,
    ExistingRefcheckAdapter,
    MetadataLookupResult,
    OfflineMetadataVerifier,
    select_authoritative_verification,
    verify_reference,
    verify_reference_with_retries,
)
from schemas.citation import ReferenceRecord, VerificationStatus


def reference_record(
    *,
    record_id: str = "R-1-deadbeef",
    doi: str = "10.1000/example",
    title: str = "A Study",
    year: int | None = 2024,
) -> ReferenceRecord:
    return ReferenceRecord(
        id=record_id,
        reference_number=1,
        source_evidence_ids=["P08-L014"],
        raw_text="[1] A Study. 2024.",
        doi=doi,
        title=title,
        year=year,
        parse_confidence=0.9,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" doi:10.1000/ABC. ", "10.1000/abc"),
        ("https://doi.org/10.1000/ABC;", "10.1000/abc"),
        ("http://dx.doi.org/10.1000/foo(bar).", "10.1000/foo(bar)"),
        ("https://doi.org/10.1000/foo).", "10.1000/foo"),
    ],
)
def test_normalize_doi_removes_variants_and_keeps_balanced_parentheses(value: str, expected: str) -> None:
    assert normalize_doi(value) == expected


def test_normalize_title_uses_nfkc_and_unicode_punctuation() -> None:
    assert normalize_title("  CAF\u00c9\u3000\uff1a A \u2014 Study! ") == "caf\u00e9 a study"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("a b c d e f g h i j k l m n o p q r s t", "a b c d e f g h i j k l m n o p q", 0.85),
        (
            "a b c d e f g h i j k l m n o p q r s t",
            "a b c d e f g h i j k l m n o p q r s",
            0.95,
        ),
        ("same title", "same title", 1.0),
    ],
)
def test_title_similarity_is_token_set_based(left: str, right: str, expected: float) -> None:
    assert title_similarity(left, right) == expected


def test_title_similarity_verification_thresholds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    reference = reference_record(doi="", title="A Study")
    adapter = OfflineMetadataVerifier([{"id": "external-1", "title": "A Study"}])

    monkeypatch.setattr("peerassist.citation_verification.title_similarity", lambda *_: 0.85)
    assert verify_reference(reference, adapter, artifact_dir=tmp_path).status is VerificationStatus.AMBIGUOUS

    monkeypatch.setattr("peerassist.citation_verification.title_similarity", lambda *_: 0.9499)
    assert verify_reference(reference, adapter, artifact_dir=tmp_path).status is VerificationStatus.AMBIGUOUS

    monkeypatch.setattr("peerassist.citation_verification.title_similarity", lambda *_: 0.95)
    assert verify_reference(reference, adapter, artifact_dir=tmp_path).status is VerificationStatus.COMPLETED


def test_compare_metadata_accepts_online_print_year_alias() -> None:
    differences = compare_reference_metadata(
        reference_record(year=2023),
        {"doi": "10.1000/example", "title": "A Study", "online_year": 2022, "print_year": 2023},
    )

    year = next(diff for diff in differences if diff.field == "year")
    assert year.comparison.value == "match"
    assert year.rule == "year_exact_or_online_print_alias"


def test_exact_doi_does_not_hide_year_mismatch(tmp_path: Path) -> None:
    verification = verify_reference(
        reference_record(doi="https://doi.org/10.X/A", year=2023),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.x/a", "year": 2024, "title": "A Study"}]),
        artifact_dir=tmp_path,
    )

    assert verification.status is VerificationStatus.COMPLETED
    assert any(diff.field == "year" and diff.comparison.value == "mismatch" for diff in verification.field_differences)


def test_no_and_multiple_candidates_have_explicit_statuses(tmp_path: Path) -> None:
    reference = reference_record()
    not_found = verify_reference(reference, OfflineMetadataVerifier([]), artifact_dir=tmp_path)
    ambiguous = verify_reference(
        reference,
        OfflineMetadataVerifier(
            [
                {"id": "external-1", "doi": "10.1000/example"},
                {"id": "external-2", "doi": "10.1000/example"},
            ]
        ),
        artifact_dir=tmp_path,
    )

    assert not_found.status is VerificationStatus.NOT_FOUND
    assert not_found.match is not None and not_found.match.candidate_count == 0
    assert ambiguous.status is VerificationStatus.AMBIGUOUS
    assert ambiguous.match is not None and ambiguous.match.candidate_count == 2


def test_missing_adapter_or_usable_input_is_unavailable_without_response_data(tmp_path: Path) -> None:
    missing_input = verify_reference(reference_record(doi="", title="", year=None), None, artifact_dir=tmp_path)
    missing_adapter = verify_reference(reference_record(), None, artifact_dir=tmp_path)

    for verification in (missing_input, missing_adapter):
        assert verification.status is VerificationStatus.UNAVAILABLE
        assert verification.error_code == "adapter_unavailable"
        assert verification.raw_response_artifact is None
        assert verification.match is None
        assert verification.source_record is None
        assert verification.observed_metadata == {}
        assert verification.field_differences == []


def test_existing_refcheck_rejects_malformed_schema_without_retry(tmp_path: Path) -> None:
    raw_response = {"ok": True, "issues": "not-a-list"}
    adapter = ExistingRefcheckAdapter(raw_response)
    verification = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "adapter_schema_error"
    assert verification.attempt_number == 1
    assert verification.raw_response_artifact is not None
    artifact = tmp_path / verification.raw_response_artifact.path
    assert json.loads(artifact.read_text(encoding="utf-8")) == raw_response


def test_malformed_received_response_is_preserved_unchanged_in_failed_artifact(tmp_path: Path) -> None:
    raw_response = {"received": ["this", "payload"], "candidates": "not-a-list"}
    adapter = SequencedAdapter([MetadataLookupResult(candidates="not-a-list", raw_response=raw_response)])  # type: ignore[arg-type]

    verification = verify_reference(reference_record(), adapter, artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "adapter_schema_error"
    assert verification.raw_response_artifact is not None
    artifact = tmp_path / verification.raw_response_artifact.path
    assert json.loads(artifact.read_text(encoding="utf-8")) == raw_response
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == verification.raw_response_artifact.sha256


def test_no_received_response_has_no_artifact(tmp_path: Path) -> None:
    verification = verify_reference(
        reference_record(),
        SequencedAdapter([CitationAdapterError("timeout", retryable=True)]),
        artifact_dir=tmp_path,
    )

    assert verification.status is VerificationStatus.FAILED
    assert verification.raw_response_artifact is None
    assert not (tmp_path / "citation_verifications").exists()


def test_raw_response_is_atomic_immutable_and_hashed(tmp_path: Path) -> None:
    verification = verify_reference(
        reference_record(),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example", "url": "https://example.test"}]),
        artifact_dir=tmp_path,
    )

    assert verification.raw_response_artifact is not None
    artifact = tmp_path / verification.raw_response_artifact.path
    assert artifact.is_file()
    assert not list(artifact.parent.glob("*.tmp"))
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == verification.raw_response_artifact.sha256
    with pytest.raises(FileExistsError):
        verify_reference(
            reference_record(),
            OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}]),
            artifact_dir=tmp_path,
            attempt_id=verification.attempt_id,
        )


class SequencedAdapter:
    name = "sequence"
    version = "1"

    def __init__(self, outcomes: list[MetadataLookupResult | CitationAdapterError]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def lookup(self, record: ReferenceRecord) -> MetadataLookupResult:
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, CitationAdapterError):
            raise outcome
        return outcome


def result(*candidates: dict[str, object]) -> MetadataLookupResult:
    return MetadataLookupResult(candidates=list(candidates), raw_response={"candidates": list(candidates)})


def test_timeout_then_success_keeps_all_attempts(tmp_path: Path) -> None:
    adapter = SequencedAdapter(
        [CitationAdapterError("timeout", retryable=True), result({"id": "external-1", "doi": "10.1000/example"})]
    )
    attempts: list = []
    verification = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts)

    assert verification.status is VerificationStatus.COMPLETED
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]
    assert len(list((tmp_path / "citation_verifications").glob("attempt-*.json"))) == 1


def test_retries_stop_after_three_timeouts_and_retain_failed_artifacts(tmp_path: Path) -> None:
    adapter = SequencedAdapter([CitationAdapterError("timeout", retryable=True) for _ in range(3)])
    attempts: list = []
    verification = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts)

    assert verification.status is VerificationStatus.FAILED
    assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]
    assert not (tmp_path / "citation_verifications").exists()


def test_retained_failed_attempts_count_toward_max_attempts(tmp_path: Path) -> None:
    existing_adapter = SequencedAdapter([CitationAdapterError("timeout", retryable=True) for _ in range(2)])
    attempts = [
        verify_reference(reference_record(), existing_adapter, artifact_dir=tmp_path, attempt_number=1),
        verify_reference(reference_record(), existing_adapter, artifact_dir=tmp_path, attempt_number=2),
    ]
    adapter = SequencedAdapter([CitationAdapterError("timeout", retryable=True), CitationAdapterError("timeout", retryable=True)])

    verification = verify_reference_with_retries(
        reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts, max_attempts=3
    )

    assert verification.status is VerificationStatus.FAILED
    assert adapter.calls == 1
    assert [attempt.attempt_number for attempt in attempts] == [1, 2, 3]


def test_retained_attempt_at_max_prevents_new_adapter_call(tmp_path: Path) -> None:
    existing_adapter = SequencedAdapter([CitationAdapterError("timeout", retryable=True) for _ in range(3)])
    attempts = [
        verify_reference(reference_record(), existing_adapter, artifact_dir=tmp_path, attempt_number=number)
        for number in (1, 2, 3)
    ]
    adapter = SequencedAdapter([result({"id": "external-1", "doi": "10.1000/example"})])

    verification = verify_reference_with_retries(
        reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts, max_attempts=3
    )

    assert verification == attempts[-1]
    assert adapter.calls == 0


def test_adapter_unavailable_is_terminal_without_retry_or_response_data(tmp_path: Path) -> None:
    adapter = SequencedAdapter([CitationAdapterError("adapter_unavailable"), result()])
    attempts: list = []

    verification = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts)

    assert verification.status is VerificationStatus.UNAVAILABLE
    assert verification.error_code == "adapter_unavailable"
    assert verification.raw_response_artifact is None
    assert verification.match is None
    assert verification.source_record is None
    assert verification.observed_metadata == {}
    assert verification.field_differences == []
    assert adapter.calls == 1
    assert attempts == [verification]


def test_not_found_does_not_retry(tmp_path: Path) -> None:
    adapter = SequencedAdapter([result(), CitationAdapterError("timeout", retryable=True)])
    attempts: list = []

    verification = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts)

    assert verification.status is VerificationStatus.NOT_FOUND
    assert adapter.calls == 1
    assert len(attempts) == 1


def test_completed_attempt_with_valid_hash_is_reused_without_adapter_call(tmp_path: Path) -> None:
    first = verify_reference(
        reference_record(),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}]),
        artifact_dir=tmp_path,
    )
    adapter = SequencedAdapter([CitationAdapterError("should_not_call")])
    adapter.name = "offline"

    reused = verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=[first])

    assert reused == first
    assert adapter.calls == 0


def test_authoritative_selection_prefers_latest_valid_terminal_over_failures(tmp_path: Path) -> None:
    completed = verify_reference(
        reference_record(),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}]),
        artifact_dir=tmp_path,
        attempt_number=2,
    )
    failed = verify_reference(
        reference_record(),
        SequencedAdapter([CitationAdapterError("timeout", retryable=True)]),
        artifact_dir=tmp_path,
        attempt_number=3,
    )
    later_not_found = verify_reference(
        reference_record(), OfflineMetadataVerifier([]), artifact_dir=tmp_path, attempt_number=4
    )

    assert select_authoritative_verification([failed, completed]) == completed
    assert select_authoritative_verification([later_not_found, failed, completed]) == later_not_found


def test_stable_verification_id_does_not_depend_on_attempt(tmp_path: Path) -> None:
    first = verify_reference(
        reference_record(), OfflineMetadataVerifier([]), artifact_dir=tmp_path, attempt_number=1
    )
    second = verify_reference(
        reference_record(), OfflineMetadataVerifier([]), artifact_dir=tmp_path, attempt_number=2
    )

    assert first.id == second.id
    assert first.attempt_id != second.attempt_id


def test_existing_refcheck_maps_explicit_external_metadata_without_network(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "total_refs": 1,
        "errors": 0,
        "warnings": 1,
        "unverified": 0,
        "error_message": "",
        "issues": [
            {
                "severity": "warning",
                "reference_title": "A Study",
                "reference_year": "2024",
                "verified_url": "https://example.test/record",
                "external_metadata": {"id": "record-1", "doi": "10.1000/example", "title": "A Study"},
            }
        ],
        "error_details": [],
        "warning_details": [],
        "unverified_details": [],
        "report_file": "",
    }

    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(payload), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.COMPLETED
    assert verification.source_record is not None and verification.source_record.id == "record-1"
    assert json.loads((tmp_path / verification.raw_response_artifact.path).read_text(encoding="utf-8")) == payload
