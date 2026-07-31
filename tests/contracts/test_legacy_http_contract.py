from __future__ import annotations

import hashlib
import http.client
import json
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import Any
from uuid import UUID

import pymupdf

from common.pipeline_context import peerassist_stage_dir, write_json_file
from peerassist.confirmation_server import create_confirmation_server
from peerassist.confirmations import build_confirmation_bundle, build_confirmation_review_queue
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.review_job_api import create_review_job_server
from schemas.peerassist import Concern
from schemas.peerassist_jobs import PaperRecord, ReviewJobState

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
PAPER_SHA256 = "d5423e88a0a29b4cc70e8007884da9ca6d9df0bdaf235a2db495ac448ac2b681"
CONTRACT = json.loads(
    (FIXTURES / "legacy_http_contract.v1.json").read_text(encoding="utf-8")
)


def _route_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
    return re.compile(f"^{pattern}$")


def _contract_route(method: str, path: str) -> dict[str, Any]:
    route_path = path.split("?", 1)[0]
    matches = [
        route
        for route in CONTRACT["routes"]
        if route["method"] == method and _route_pattern(route["path"]).fullmatch(route_path)
    ]
    assert len(matches) == 1, f"missing or ambiguous contract for {method} {route_path}"
    return matches[0]


def _field(payload: Any, dotted_path: str) -> Any:
    current = payload
    for segment in dotted_path.split("."):
        assert isinstance(current, dict) and segment in current, f"missing field {dotted_path}"
        current = current[segment]
    return current


def _assert_contract_response(
    method: str,
    path: str,
    status: int,
    headers: dict[str, str],
    content: bytes,
) -> None:
    route = _contract_route(method, path)
    allowed = {
        int(route[name])
        for name in ("status", "range_status", "conflict_status", "interruption_status")
        if name in route
    }
    assert status in allowed
    for name in route["required_headers"]:
        assert name.lower() in headers, f"missing header {name} for {method} {path}"
    if status == route["status"] and route["required_fields"]:
        payload = json.loads(content.decode("utf-8"))
        for name in route["required_fields"]:
            _field(payload, name)


class _NoopScheduler:
    def submit(self, job_id: UUID | str) -> None:
        del job_id

    def shutdown(self, *, wait: bool = True) -> None:
        del wait


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers={"Connection": "close", **(headers or {})})
    response = connection.getresponse()
    result = response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    connection.close()
    _assert_contract_response(method, path, *result)
    return result


def _json_request(
    port: int,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    body = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
    status, headers, content = _request(
        port,
        method,
        path,
        body=body,
        headers={"Content-Type": "application/json"} if body is not None else None,
    )
    return status, headers, json.loads(content.decode("utf-8"))


@contextmanager
def _running(server: Any):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _seed_paper(data_dir: Path) -> bytes:
    content = (FIXTURES / "public_test.pdf").read_bytes()
    target = data_dir / "papers" / PAPER_SHA256 / "source" / "source.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    PaperRepository(data_dir).create_or_get(
        PaperRecord(
            paper_id=PAPER_SHA256,
            source_pdf_name="public_test.pdf",
            source_pdf_path="source/source.pdf",
            size_bytes=len(content),
        )
    )
    return content


def _seed_workspace(data_dir: Path) -> ReviewJobState:
    state = ReviewJobState.model_validate(_load("review_job.v1.json"))
    repository = ReviewJobRepository(data_dir)
    repository.create(state)
    concern = Concern.model_validate(_load("concern.v1.json"))
    out_dir = peerassist_stage_dir(data_dir / state.run_dir)
    evidence = {
        "items": [
            {"id": "P01-L001", "type": "text_span", "page": 1, "locator": "page 1", "text": "Public synthetic evidence.", "bbox": [36, 60, 250, 80]},
            {"id": "M-1", "type": "citation", "page": 1, "locator": "page 1, citation [1]", "text": "[1]", "bbox": [36, 90, 50, 105]},
            {"id": "R-E-1", "type": "reference", "page": 1, "locator": "page 1, reference 1", "text": "[1] Public Synthetic Study. 2026.", "bbox": [36, 110, 250, 125]},
        ]
    }
    bundle = build_confirmation_bundle(
        concerns=[concern], evidence_lookup={"P01-L001": "page 1"}
    )
    write_json_file(out_dir / "peerassist_concerns.json", {"paper_id": PAPER_SHA256, "concerns": [concern.model_dump(mode="json")]})
    write_json_file(out_dir / "evidence_ledger.json", evidence)
    write_json_file(out_dir / "confirmation_bundle.json", bundle)
    write_json_file(out_dir / "confirmation_review_queue.json", build_confirmation_review_queue(bundle))
    write_json_file(out_dir / "human_confirmations.json", {"schema_version": "peerassist.human_confirmations.v2", "revision": 0, "mutation_revision": 0, "actions": []})
    write_json_file(out_dir / "citation_audit.json", _load("citation.v1.json"))
    write_json_file(out_dir / "agent_results.json", {"results": []})
    write_json_file(out_dir / "capability_invocations.json", {"results": []})
    return state


def _seed_transition_job(
    data_dir: Path, *, job_id: str, status: str, stage: str = "await_confirmation", **changes: Any
) -> ReviewJobState:
    state = ReviewJobState(
        id=UUID(job_id),
        paper_id=PAPER_SHA256,
        run_dir=f"jobs/{job_id}/run",
        attempt_id=f"attempt-{job_id[-4:]}",
        status=status,
        stage=stage,
        **changes,
    )
    return ReviewJobRepository(data_dir).create(state)


def test_legacy_http_snapshot_declares_the_complete_supported_surface() -> None:
    contract = CONTRACT
    assert contract["schema_version"] == "peerassist.legacy_http_contract.v1"
    actual = {(route["method"], route["path"]) for route in contract["routes"]}
    expected = {
        ("GET", "/"),
        ("GET", "/paper"),
        ("GET", "/api/bootstrap"),
        ("GET", "/api/state"),
        ("GET", "/api/events"),
        ("GET", "/paper.pdf"),
        ("GET", "/api/health"),
        ("GET", "/api/papers/{paper_id}/source"),
        ("POST", "/api/papers/upload"),
        ("POST", "/api/reviews"),
        ("GET", "/api/jobs"),
        ("GET", "/api/jobs/{job_id}"),
        ("GET", "/api/jobs/{job_id}/events"),
        ("GET", "/api/jobs/{job_id}/workspace"),
        ("POST", "/api/jobs/{job_id}/decisions"),
        ("POST", "/api/jobs/{job_id}/consents/{service}"),
        ("POST", "/api/jobs/{job_id}/cancel"),
        ("POST", "/api/jobs/{job_id}/retry"),
        ("POST", "/api/jobs/{job_id}/finalize"),
        ("GET", "/api/jobs/{job_id}/artifacts"),
        ("GET", "/api/jobs/{job_id}/artifacts/{artifact}"),
    }
    assert actual == expected
    for route in contract["routes"]:
        assert route["owner"] in {"workspace", "review-api"}
        assert isinstance(route["status"], int)
        assert "request_fixture" in route
        assert isinstance(route["required_fields"], list)
        assert isinstance(route["required_headers"], list)
        assert route["expected"]
        assert route["mutation"] in {"read", "create", "transition", "approval", "finalize"}
        assert route["idempotency"] in {"safe", "keyed", "revision-guarded", "state-guarded"}


def test_contract_fixtures_are_public_deterministic_and_self_consistent() -> None:
    pdf = (FIXTURES / "public_test.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-")
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        assert document.page_count == 1
        assert document[0].get_text().strip() == "PeerAssist public synthetic contract fixture"
    assert len(pdf) < 16_384

    for name, schema in {
        "review_job.v1.json": "peerassist.review_job.v2",
        "concern.v1.json": "peerassist.concern.v1",
        "citation.v1.json": "peerassist.citation_audit.v1",
        "finalized_report.v1.json": "peerassist.finalized_report_fixture.v1",
    }.items():
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert payload["schema_version"] == schema

    assert hashlib.sha256(pdf).hexdigest() == PAPER_SHA256


def test_runtime_response_validation_is_driven_by_the_json_contract(
    tmp_path: Path,
) -> None:
    route = _contract_route("GET", "/api/health")
    original = list(route["required_fields"])
    route["required_fields"].append("field_that_cannot_exist")
    server = create_review_job_server(data_dir=tmp_path, port=0)
    try:
        with _running(server) as port:
            try:
                _json_request(port, "GET", "/api/health")
            except AssertionError as exc:
                assert "field_that_cannot_exist" in str(exc)
            else:
                raise AssertionError("mutated JSON contract did not fail a real response")
    finally:
        route["required_fields"] = original


def test_workspace_routes_and_pdf_bytes_match_the_frozen_contract(tmp_path: Path) -> None:
    run_dir = tmp_path / "workspace" / "run"
    run_dir.mkdir(parents=True)
    pdf = (FIXTURES / "public_test.pdf").read_bytes()
    shutil.copyfile(FIXTURES / "public_test.pdf", run_dir / "public_test.pdf")
    server = create_confirmation_server(run_dir=run_dir, paper_id="public_test", port=0)
    with _running(server) as port:
        for path in ("/", "/paper"):
            status, headers, body = _request(port, "GET", path)
            assert status == 200
            assert headers["content-type"].startswith("text/html")
            assert b"__PEERASSIST_BOOTSTRAP__" not in body
        status, _, bootstrap = _json_request(port, "GET", "/api/bootstrap")
        assert status == 200
        assert bootstrap["schema_version"] == "peerassist.workspace_bootstrap.v1"
        assert bootstrap["assets"]["pdf_url"] == "/paper.pdf"
        status, _, state = _json_request(port, "GET", "/api/state")
        assert status == 200
        assert state["schema_version"] == "peerassist.confirmation_state.v1"
        status, headers, events = _request(port, "GET", "/api/events")
        assert status == 200
        assert headers["content-type"].startswith("text/event-stream")
        assert b"event: state" in events

        status, headers, body = _request(port, "GET", "/paper.pdf")
        assert status == 200
        assert int(headers["content-length"]) == len(pdf)
        assert hashlib.sha256(body).hexdigest() == PAPER_SHA256
        status, headers, body = _request(port, "GET", "/paper.pdf", headers={"Range": "bytes=0-31"})
        assert status == 206
        assert headers["content-range"] == f"bytes 0-31/{len(pdf)}"
        assert int(headers["content-length"]) == 32
        assert body == pdf[:32]
        assert hashlib.sha256(body).hexdigest() == hashlib.sha256(pdf[:32]).hexdigest()


def test_review_api_freezes_create_read_approval_transition_and_artifact_contracts(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "repository"
    pdf = _seed_paper(data_dir)
    server = create_review_job_server(data_dir=data_dir, port=0)
    server.review_service.scheduler.shutdown(wait=True)
    server.review_service.scheduler = _NoopScheduler()
    primary = _seed_workspace(data_dir)
    consent = _seed_transition_job(
        data_dir,
        job_id="00000000-0000-4000-8000-000000000002",
        status="blocked",
        stage="agents",
        blocked_reason="approval_required",
        required_consents=["model"],
        resume_stage="agents",
    )
    cancellable = _seed_transition_job(
        data_dir,
        job_id="00000000-0000-4000-8000-000000000003",
        status="awaiting_human_confirmation",
    )
    retryable = _seed_transition_job(
        data_dir,
        job_id="00000000-0000-4000-8000-000000000004",
        status="cancelled",
    )

    with _running(server) as port:
        status, _, health = _json_request(port, "GET", "/api/health")
        assert status == 200
        assert health == {"status": "ok", "service": "peerassist-review-jobs"}
        status, headers, body = _request(port, "GET", f"/api/papers/{PAPER_SHA256}/source")
        assert status == 200
        assert int(headers["content-length"]) == len(pdf)
        assert body == pdf
        status, headers, body = _request(port, "GET", f"/api/papers/{PAPER_SHA256}/source", headers={"Range": "bytes=0-31"})
        assert status == 206
        assert headers["content-range"] == f"bytes 0-31/{len(pdf)}"
        assert body == pdf[:32]

        review_payload = {"paper_id": PAPER_SHA256, "mode": "fast", "idempotency_key": "contract-create-v1"}
        status, _, created = _json_request(port, "POST", "/api/reviews", review_payload)
        assert status == 202
        status, _, repeated = _json_request(port, "POST", "/api/reviews", review_payload)
        assert status == 202
        assert repeated["job"]["id"] == created["job"]["id"]
        assert repeated["job"]["paper_id"] == PAPER_SHA256

        boundary = "peerassist-contract-boundary"
        upload = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="public_test.pdf"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            pdf,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        status, _, uploaded_body = _request(port, "POST", "/api/papers/upload", body=upload, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert status == 202
        uploaded = json.loads(uploaded_body)
        status, _, uploaded_again_body = _request(port, "POST", "/api/papers/upload", body=upload, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        assert status == 202
        uploaded_again = json.loads(uploaded_again_body)
        assert uploaded["paper"]["paper_id"] == PAPER_SHA256
        assert uploaded_again["job"]["id"] == uploaded["job"]["id"]
        stored = data_dir / "papers" / PAPER_SHA256 / "source" / "source.pdf"
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == PAPER_SHA256

        status, _, jobs = _json_request(port, "GET", "/api/jobs")
        assert status == 200
        assert str(primary.id) in {job["id"] for job in jobs["jobs"]}
        status, _, detail = _json_request(port, "GET", f"/api/jobs/{primary.id}")
        assert status == 200
        assert detail["job"]["status"] == "awaiting_human_confirmation"
        status, headers, events = _request(port, "GET", f"/api/jobs/{primary.id}/events")
        assert status == 200
        assert headers["content-type"].startswith("text/event-stream")
        assert events.endswith(b"event: heartbeat\ndata: {}\n\n")
        status, _, workspace = _json_request(port, "GET", f"/api/jobs/{primary.id}/workspace")
        assert status == 200, workspace
        assert workspace["state"]["pending_count"] == 1
        assert workspace["state"]["queue"]["items"][0]["status"] == "pending_human_confirmation"
        assert workspace["state"]["citation_audit"]["links"][0]["id"] == "L-CONTRACT-1"

        concern = _load("concern.v1.json")
        decision_payload = {
            "concern_id": concern["id"],
            "finding_lineage_id": concern["finding_lineage_id"],
            "finding_id": concern["finding_id"],
            "finding_revision": concern["revision"],
            "confirmation_revision": 0,
            "action": "confirm",
            "reviewer_id": "contract-reviewer",
            "timestamp": "2026-07-17T00:01:00Z",
        }
        status, _, decided = _json_request(port, "POST", f"/api/jobs/{primary.id}/decisions", decision_payload)
        assert status == 200
        assert decided["job"]["confirmation_revision"] == 1
        assert decided["job"]["revision"] == 1
        assert decided["job"]["last_event_id"] == 1
        assert decided["state"]["pending_count"] == 0
        assert decided["state"]["queue"]["items"][0]["status"] == "confirmed"
        status, _, stale = _json_request(port, "POST", f"/api/jobs/{primary.id}/decisions", decision_payload)
        assert status == 409
        assert stale["result"]["error_code"] == "revision_conflict"
        assert stale["job"]["revision"] == 1
        assert stale["job"]["last_event_id"] == 1

        consent_path = f"/api/jobs/{consent.id}/consents/model"
        status, _, granted = _json_request(port, "POST", consent_path, {"decision": "granted", "actor": "contract-reviewer"})
        assert status == 200
        status, _, granted_again = _json_request(port, "POST", consent_path, {"decision": "granted", "actor": "contract-reviewer"})
        assert status == 200
        assert granted_again["job"]["revision"] == granted["job"]["revision"] == 1
        assert ReviewJobRepository(data_dir).get(consent.id).last_event_id == 1

        cancel_path = f"/api/jobs/{cancellable.id}/cancel"
        status, _, cancelled = _json_request(port, "POST", cancel_path, {})
        assert status == 200
        status, _, cancelled_again = _json_request(port, "POST", cancel_path, {})
        assert status == 200
        assert cancelled_again["job"]["revision"] == cancelled["job"]["revision"] == 1
        assert cancelled_again["job"]["status"] == "cancel_requested"
        assert ReviewJobRepository(data_dir).get(cancellable.id).last_event_id == 1

        retry_path = f"/api/jobs/{retryable.id}/retry"
        status, _, retried = _json_request(port, "POST", retry_path, {})
        assert status == 200
        status, _, retried_again = _json_request(port, "POST", retry_path, {})
        assert status == 200
        assert retried_again["job"]["revision"] == retried["job"]["revision"] == 1
        assert retried_again["job"]["status"] == "queued"
        assert ReviewJobRepository(data_dir).get(retryable.id).last_event_id == 1

        finalize_path = f"/api/jobs/{primary.id}/finalize"
        status, _, finalized = _json_request(port, "POST", finalize_path, {"confirmation_revision": 1})
        assert status == 200
        assert finalized["job"]["status"] == "completed"
        assert finalized["job"]["stage"] == "complete"
        first_final_revision = finalized["job"]["revision"]
        first_final_event = ReviewJobRepository(data_dir).get(primary.id).last_event_id
        status, _, finalized_again = _json_request(port, "POST", finalize_path, {"confirmation_revision": 1})
        assert status == 200
        assert finalized_again["job"]["revision"] == first_final_revision
        assert finalized_again["job"]["last_event_id"] == first_final_event
        assert ReviewJobRepository(data_dir).get(primary.id).last_event_id == first_final_event
        status, _, stale_finalize = _json_request(
            port, "POST", finalize_path, {"confirmation_revision": 0}
        )
        assert status == 409
        assert stale_finalize["error"] == "confirmation_revision_conflict"
        unchanged = ReviewJobRepository(data_dir).get(primary.id)
        assert unchanged.status.value == "completed"
        assert unchanged.stage.value == "complete"
        assert unchanged.revision == first_final_revision
        assert unchanged.last_event_id == first_final_event

        status, _, artifact_index = _json_request(port, "GET", f"/api/jobs/{primary.id}/artifacts")
        assert status == 200
        expected_final = _load("finalized_report.v1.json")
        assert artifact_index["artifacts"]["ready"] is True
        assert artifact_index["artifacts"]["report_version"] == expected_final["report_version"]
        items = {item["name"]: item for item in artifact_index["artifacts"]["items"]}
        assert set(items) == set(expected_final["required_artifacts"])
        assert {name: item["sha256"] for name, item in items.items()} == expected_final[
            "artifact_sha256"
        ]
        for name, item in items.items():
            status, headers, content = _request(port, "GET", f"/api/jobs/{primary.id}/artifacts/{name}")
            assert status == 200
            assert int(headers["content-length"]) == item["size_bytes"]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
        report = json.loads(_request(port, "GET", f"/api/jobs/{primary.id}/artifacts/report_en_json")[2])
        assert report["confirmation_revision"] == 1
        assert report["concerns"][0]["evidence_ids"] == expected_final["required_evidence_ids"]
        assert expected_final["required_citation_ids"][0] in report["concerns"][0]["metadata"]["citation_finding_ids"]


def test_decision_event_is_reconciled_after_an_interrupted_audit_append(
    tmp_path: Path, monkeypatch: Any
) -> None:
    data_dir = tmp_path / "repository"
    _seed_paper(data_dir)
    primary = _seed_workspace(data_dir)
    server = create_review_job_server(data_dir=data_dir, port=0)
    server.review_service.scheduler.shutdown(wait=True)
    server.review_service.scheduler = _NoopScheduler()
    concern = _load("concern.v1.json")
    payload = {
        "concern_id": concern["id"],
        "finding_lineage_id": concern["finding_lineage_id"],
        "finding_id": concern["finding_id"],
        "finding_revision": concern["revision"],
        "confirmation_revision": 0,
        "action": "confirm",
        "reviewer_id": "contract-reviewer",
        "timestamp": "2026-07-17T00:01:00Z",
    }
    original_append = server.review_service.repository.append_event_once

    def fail_append(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("simulated audit append interruption")

    monkeypatch.setattr(server.review_service.repository, "append_event_once", fail_append)
    with _running(server) as port:
        status, _, failed = _json_request(
            port, "POST", f"/api/jobs/{primary.id}/decisions", payload
        )
        assert status == 500
        assert failed["error"] == "decision_commit_interrupted"
        persisted = ReviewJobRepository(data_dir).get(primary.id)
        assert persisted.confirmation_revision == 1
        assert persisted.last_event_id == 0

        monkeypatch.setattr(server.review_service.repository, "append_event_once", original_append)
        status, _, recovered = _json_request(port, "GET", f"/api/jobs/{primary.id}")
        assert status == 200
        assert recovered["job"]["confirmation_revision"] == 1
        assert recovered["job"]["last_event_id"] == 1
        events = ReviewJobRepository(data_dir).replay_events(primary.id)
        assert [(event.event_type, event.payload["confirmation_revision"]) for event in events] == [
            ("confirmation_decision_applied", 1)
        ]


def test_decision_state_is_reconciled_after_an_interrupted_job_update(
    tmp_path: Path, monkeypatch: Any
) -> None:
    data_dir = tmp_path / "repository"
    _seed_paper(data_dir)
    primary = _seed_workspace(data_dir)
    server = create_review_job_server(data_dir=data_dir, port=0)
    server.review_service.scheduler.shutdown(wait=True)
    server.review_service.scheduler = _NoopScheduler()
    concern = _load("concern.v1.json")
    payload = {
        "concern_id": concern["id"],
        "finding_lineage_id": concern["finding_lineage_id"],
        "finding_id": concern["finding_id"],
        "finding_revision": concern["revision"],
        "confirmation_revision": 0,
        "action": "confirm",
        "reviewer_id": "contract-reviewer",
        "timestamp": "2026-07-17T00:01:00Z",
    }
    original_update = server.review_service.repository.update

    def fail_update(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("simulated aggregate update interruption")

    monkeypatch.setattr(server.review_service.repository, "update", fail_update)
    with _running(server) as port:
        status, _, failed = _json_request(
            port, "POST", f"/api/jobs/{primary.id}/decisions", payload
        )
        assert status == 500
        assert failed["error"] == "decision_commit_interrupted"
        persisted = ReviewJobRepository(data_dir).get(primary.id)
        assert persisted.confirmation_revision == 0
        assert persisted.last_event_id == 0

        monkeypatch.setattr(server.review_service.repository, "update", original_update)
        status, _, recovered = _json_request(port, "GET", f"/api/jobs/{primary.id}")
        assert status == 200
        assert recovered["job"]["confirmation_revision"] == 1
        assert recovered["job"]["last_event_id"] == 1
