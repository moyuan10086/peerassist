from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from peerassist.citation_artifacts import (
    ArtifactPathError,
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
from peerassist.citation_verification import (
    CitationAdapterError,
    ExistingRefcheckAdapter,
    MetadataLookupResult,
    OfflineMetadataVerifier,
    select_authoritative_verification,
    verify_reference,
    verify_reference_with_retries,
)
from schemas.citation import RawResponseArtifact, ReferenceRecord, VerificationStatus


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
        (" doi:10.1000/ABC. ", "10.1000/abc."),
        ("https://doi.org/10.1000/ABC;", "10.1000/abc;"),
        ("http://dx.doi.org/10.1000/foo(bar).", "10.1000/foo(bar)."),
        ("https://doi.org/10.1000/foo).", "10.1000/foo)."),
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
    duplicate = verify_reference(
        reference_record(),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}]),
        artifact_dir=tmp_path,
        attempt_id=verification.attempt_id,
    )
    assert duplicate.status is VerificationStatus.FAILED
    assert duplicate.error_code == "artifact_write_error"


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


def test_retry_ceiling_excludes_corrupt_cached_terminal_artifact(tmp_path: Path) -> None:
    failed = verify_reference(
        reference_record(),
        SequencedAdapter([CitationAdapterError("timeout", retryable=True)]),
        artifact_dir=tmp_path,
        attempt_number=1,
    )
    completed_adapter = OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}])
    completed_adapter.name = "sequence"
    corrupt_completed = verify_reference(
        reference_record(),
        completed_adapter,
        artifact_dir=tmp_path,
        attempt_number=3,
    )
    assert corrupt_completed.raw_response_artifact is not None
    (tmp_path / corrupt_completed.raw_response_artifact.path).write_text("corrupt", encoding="utf-8")
    adapter = SequencedAdapter([result({"id": "external-2", "doi": "10.1000/example"})])

    verification = verify_reference_with_retries(
        reference_record(), adapter, artifact_dir=tmp_path, attempts=[failed, corrupt_completed], max_attempts=3
    )

    assert verification == failed
    assert adapter.calls == 0


def test_retry_ceiling_with_only_invalid_cache_returns_failed_verification(tmp_path: Path) -> None:
    cached_adapter = OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}])
    cached_adapter.name = "sequence"
    corrupt = verify_reference(reference_record(), cached_adapter, artifact_dir=tmp_path, attempt_number=1)
    assert corrupt.raw_response_artifact is not None
    (tmp_path / corrupt.raw_response_artifact.path).write_bytes(b"corrupt")
    adapter = SequencedAdapter([result({"id": "external-2", "doi": "10.1000/example"})])

    verification = verify_reference_with_retries(
        reference_record(), adapter, artifact_dir=tmp_path, attempts=[corrupt], max_attempts=1
    )

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "artifact_invalid"
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


def refcheck_payload(*issues: dict[str, object], ok: bool = True, error_message: str = "") -> dict[str, object]:
    return {
        "ok": ok,
        "total_refs": 1,
        "errors": sum(issue["severity"] == "error" for issue in issues),
        "warnings": sum(issue["severity"] == "warning" for issue in issues),
        "unverified": sum(issue["severity"] == "unverified" for issue in issues),
        "error_message": error_message,
        "issues": list(issues),
        "error_details": [issue for issue in issues if issue["severity"] == "error"],
        "warning_details": [issue for issue in issues if issue["severity"] == "warning"],
        "unverified_details": [issue for issue in issues if issue["severity"] == "unverified"],
        "report_file": "",
    }


def refcheck_issue(**overrides: object) -> dict[str, object]:
    issue: dict[str, object] = {
        "severity": "warning",
        "type": "incomplete::missing_doi",
        "reference_title": "A Study",
        "reference_year": "2024",
        "cited_url": "",
        "verified_url": "https://records.example/a-study",
        "details": "DOI absent",
        "raw_reference": "[1] A Study. 2024.",
        "corrected_plaintext": "",
        "corrected_bibtex": (
            "@article{a_study,\n"
            "  title = {A Study},\n"
            "  year = {2024},\n"
            "  doi = {10.1000/a-study},\n"
            "  url = {https://doi.org/10.1000/a-study}\n"
            "}"
        ),
        "corrected_bibitem": "",
    }
    issue.update(overrides)
    return issue


def test_existing_refcheck_warning_parses_real_corrected_bibtex_from_exact_path_bytes(tmp_path: Path) -> None:
    payload = refcheck_payload(refcheck_issue())
    source = tmp_path / "reference_check.json"
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    source.write_bytes(raw)

    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(source), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.COMPLETED
    assert verification.observed_metadata["doi"] == "10.1000/a-study"
    assert verification.source_record is not None
    assert verification.source_record.url == "https://records.example/a-study"
    assert verification.raw_response_artifact is not None
    assert (tmp_path / verification.raw_response_artifact.path).read_bytes() == raw


def test_existing_refcheck_deduplicates_warning_rows_for_one_external_record(tmp_path: Path) -> None:
    first = refcheck_issue(type="incomplete::missing_doi")
    second = refcheck_issue(type="incomplete::missing_venue")
    payload = refcheck_payload(first, second)

    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(payload), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.COMPLETED
    assert verification.match is not None
    assert verification.match.candidate_count == 1


def test_existing_refcheck_explicit_no_match_is_not_found_but_omitted_success_is_unavailable(tmp_path: Path) -> None:
    no_match = refcheck_payload(refcheck_issue(severity="unverified", type="unverified::no_match", corrected_bibtex=""))

    explicit = verify_reference(reference_record(), ExistingRefcheckAdapter(no_match), artifact_dir=tmp_path)
    omitted = verify_reference(reference_record(), ExistingRefcheckAdapter(refcheck_payload()), artifact_dir=tmp_path)

    assert explicit.status is VerificationStatus.NOT_FOUND
    assert omitted.status is VerificationStatus.UNAVAILABLE
    assert omitted.error_code == "adapter_unavailable"


def test_existing_refcheck_failed_source_preserves_received_error_payload(tmp_path: Path) -> None:
    payload = refcheck_payload(ok=False, error_message="backend interrupted")

    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(payload), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "source_error"
    assert verification.raw_response_artifact is not None
    assert json.loads((tmp_path / verification.raw_response_artifact.path).read_text(encoding="utf-8"))["error_message"] == "backend interrupted"


def test_existing_refcheck_malformed_path_preserves_exact_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "reference_check.json"
    raw = b'{"ok": true, broken'
    source.write_bytes(raw)

    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(source), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "adapter_schema_error"
    assert verification.raw_response_artifact is not None
    assert (tmp_path / verification.raw_response_artifact.path).read_bytes() == raw


def test_existing_refcheck_missing_path_is_unavailable(tmp_path: Path) -> None:
    verification = verify_reference(reference_record(), ExistingRefcheckAdapter(tmp_path / "missing.json"), artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.UNAVAILABLE
    assert verification.raw_response_artifact is None


def test_normalize_doi_preserves_trailing_punctuation_and_title_similarity_controls_comparison() -> None:
    assert normalize_doi(" doi:10.1000/example. ") == "10.1000/example."
    left = "a b c d e f g h i j k l m n o p q r s t"
    right = "a b c d e f g h i j k l m n o p q r s"

    difference = next(diff for diff in compare_reference_metadata(reference_record(title=left), {"title": right}) if diff.field == "title")

    assert difference.comparison.value == "match"
    assert difference.rule == "title_similarity_unique"


def test_candidate_selection_prefers_one_exact_doi_and_rejects_multiple_exact_dois(tmp_path: Path) -> None:
    one_exact = verify_reference(
        reference_record(),
        SequencedAdapter(
            [
                result(
                    {"id": "other", "doi": "10.1000/other", "title": "Other"},
                    {"id": "exact", "doi": "10.1000/example", "title": "Different"},
                )
            ]
        ),
        artifact_dir=tmp_path,
    )
    multiple_exact = verify_reference(
        reference_record(),
        SequencedAdapter(
            [
                result(
                    {"id": "one", "doi": "10.1000/example"},
                    {"id": "two", "doi": "10.1000/example"},
                )
            ]
        ),
        artifact_dir=tmp_path,
    )

    assert one_exact.status is VerificationStatus.COMPLETED
    assert one_exact.match is not None and one_exact.match.selected_candidate_id == "exact"
    assert multiple_exact.status is VerificationStatus.AMBIGUOUS


def test_artifacts_reject_traversal_and_cached_outside_or_symlink_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_response_artifact(tmp_path, "../escape", b"data")

    outside = tmp_path.parent / "outside.json"
    outside.write_bytes(b"outside")
    outside_artifact = RawResponseArtifact(path="../outside.json", sha256=hashlib.sha256(b"outside").hexdigest())
    assert not validate_response_artifact(tmp_path, outside_artifact)

    artifact_dir = tmp_path / "citation_verifications"
    artifact_dir.mkdir()
    link = artifact_dir / "attempt-link.json"
    os.symlink(outside, link)
    linked_artifact = RawResponseArtifact(path="citation_verifications/attempt-link.json", sha256=hashlib.sha256(b"outside").hexdigest())
    assert not validate_response_artifact(tmp_path, linked_artifact)


def test_artifact_validation_hashes_a_single_nofollow_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = write_response_artifact(tmp_path, "descriptor", b'{"raw":true}')

    monkeypatch.setattr(Path, "read_bytes", lambda _: (_ for _ in ()).throw(AssertionError("pathname reopened")))

    assert validate_response_artifact(tmp_path, artifact)


def test_artifact_publish_is_immutable_for_concurrent_same_attempt(tmp_path: Path) -> None:
    payload = b'{"raw":true}'
    barrier = threading.Barrier(2)

    def write() -> object:
        barrier.wait()
        try:
            return write_response_artifact(tmp_path, "same_attempt", payload)
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: write(), range(2)))

    assert sum(outcome == "exists" for outcome in outcomes) == 1
    artifact = next(outcome for outcome in outcomes if outcome != "exists")
    assert isinstance(artifact, RawResponseArtifact)
    assert (tmp_path / artifact.path).read_bytes() == payload
    assert validate_response_artifact(tmp_path, artifact)


def test_artifact_directory_rebinding_cleans_published_file_and_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = tmp_path / "citation_verifications"
    artifact_dir.mkdir()
    held_directory = tmp_path / "held-directory"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_link = os.link
    swapped = False

    def swap_then_link(*args: object, **kwargs: object) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(artifact_dir, held_directory)
            os.symlink(outside, artifact_dir, target_is_directory=True)
        original_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", swap_then_link)
    with pytest.raises(ArtifactPathError):
        write_response_artifact(tmp_path, "swap", b'{"inside":true}')

    assert not list(outside.iterdir())
    assert not list(held_directory.iterdir())


def test_rebound_artifact_directory_degrades_verification_without_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_dir = tmp_path / "citation_verifications"
    artifact_dir.mkdir()
    held_directory = tmp_path / "held-directory"
    outside = tmp_path / "outside"
    outside.mkdir()
    original_link = os.link
    swapped = False

    def swap_then_link(*args: object, **kwargs: object) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            os.rename(artifact_dir, held_directory)
            os.symlink(outside, artifact_dir, target_is_directory=True)
        original_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", swap_then_link)
    verification = verify_reference(
        reference_record(),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "10.1000/example"}]),
        artifact_dir=tmp_path,
    )

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "artifact_write_error"
    assert verification.raw_response_artifact is None
    assert not list(outside.iterdir())
    assert not list(held_directory.iterdir())


def test_artifact_directory_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, tmp_path / "citation_verifications", target_is_directory=True)

    with pytest.raises(ArtifactWriteError):
        write_response_artifact(tmp_path, "directory-link", b"data")


def test_artifact_serialization_and_write_failures_are_failed_verifications(tmp_path: Path) -> None:
    nonserializable = verify_reference(
        reference_record(),
        SequencedAdapter([MetadataLookupResult(candidates=[], raw_response=object())]),
        artifact_dir=tmp_path,
    )
    root_file = tmp_path / "not-a-directory"
    root_file.write_text("x", encoding="utf-8")
    unwritable = verify_reference(reference_record(), OfflineMetadataVerifier([]), artifact_dir=root_file)

    assert nonserializable.status is VerificationStatus.FAILED
    assert nonserializable.error_code == "artifact_serialization_error"
    assert nonserializable.raw_response_artifact is None
    assert unwritable.status is VerificationStatus.FAILED
    assert unwritable.error_code == "artifact_write_error"
    assert unwritable.raw_response_artifact is None


@pytest.mark.parametrize(
    "candidate",
    [
        {},
        {"id": object(), "doi": "10.1000/example"},
        {"id": "external-1", "doi": "10.1000/example", "nested": {"bad": object()}},
    ],
)
def test_malformed_candidate_values_fail_schema_after_persisting_serializable_raw_response(
    tmp_path: Path, candidate: dict[str, object]
) -> None:
    adapter = SequencedAdapter([MetadataLookupResult(candidates=[candidate], raw_response={"received": True})])  # type: ignore[list-item]

    verification = verify_reference(reference_record(), adapter, artifact_dir=tmp_path)

    assert verification.status is VerificationStatus.FAILED
    assert verification.error_code == "adapter_schema_error"
    assert verification.raw_response_artifact is not None
    assert json.loads((tmp_path / verification.raw_response_artifact.path).read_text(encoding="utf-8")) == {"received": True}


def test_whitespace_only_dois_are_not_exact_matches(tmp_path: Path) -> None:
    verification = verify_reference(
        reference_record(doi="   ", title="Different"),
        OfflineMetadataVerifier([{"id": "external-1", "doi": "\t", "title": "Elsewhere"}]),
        artifact_dir=tmp_path,
    )

    assert verification.status is VerificationStatus.NOT_FOUND


def test_candidate_without_id_gets_stable_fallback_and_retains_metadata_differences(tmp_path: Path) -> None:
    candidate = {"doi": "10.1000/example", "title": "Different Study", "year": 2023}
    first = verify_reference(reference_record(), OfflineMetadataVerifier([candidate]), artifact_dir=tmp_path)
    second = verify_reference(reference_record(), OfflineMetadataVerifier([candidate]), artifact_dir=tmp_path)

    assert first.status is VerificationStatus.COMPLETED
    assert second.status is VerificationStatus.COMPLETED
    assert first.match is not None and second.match is not None
    assert first.match.selected_candidate_id == second.match.selected_candidate_id
    assert {difference.field for difference in first.field_differences if difference.comparison.value == "mismatch"} >= {
        "title",
        "year",
    }


def test_non_integral_year_does_not_match_manuscript_year() -> None:
    differences = compare_reference_metadata(reference_record(year=2024), {"year": 2024.9})

    assert not any(difference.field == "year" and difference.comparison.value == "match" for difference in differences)


def test_cached_artifact_path_must_bind_to_its_own_attempt_id(tmp_path: Path) -> None:
    first = verify_reference(reference_record(), OfflineMetadataVerifier([]), artifact_dir=tmp_path, attempt_id="attempt-a")
    second = first.model_copy(update={"attempt_id": "attempt-b", "attempt_number": 2})

    authoritative = select_authoritative_verification([second], artifact_dir=tmp_path)

    assert authoritative is None


def test_retry_rejects_nonpositive_max_attempts_without_adapter_work(tmp_path: Path) -> None:
    adapter = SequencedAdapter([result()])

    with pytest.raises(ValueError, match="max_attempts"):
        verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, max_attempts=0)
    assert adapter.calls == 0


def test_concurrent_retry_callers_share_unique_attempt_numbers_and_ceiling(tmp_path: Path) -> None:
    adapter = SequencedAdapter([CitationAdapterError("timeout", retryable=True) for _ in range(3)])
    attempts: list = []
    barrier = threading.Barrier(2)

    def verify() -> object:
        barrier.wait()
        return verify_reference_with_retries(reference_record(), adapter, artifact_dir=tmp_path, attempts=attempts)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: verify(), range(2)))

    assert all(isinstance(outcome, type(outcomes[0])) for outcome in outcomes)
    assert sorted(attempt.attempt_number for attempt in attempts) == [1, 2, 3]
    assert adapter.calls == 3
