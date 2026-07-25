from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.request import Request, urlopen
from uuid import uuid4

import pymupdf

from common.pipeline_context import peerassist_stage_dir, write_json_file
from peerassist.confirmation_server import create_review_job_server
from peerassist.confirmations import build_confirmation_bundle, build_confirmation_review_queue
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from schemas.peerassist import Concern, ConcernLevel
from schemas.peerassist_jobs import PaperRecord, ReviewJobState


def _paper(data_dir: Path) -> str:
    source = data_dir / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "PeerAssist API Study\nWe show accuracy improves.")
    document.save(source)
    document.close()
    paper_id = hashlib.sha256(source.read_bytes()).hexdigest()
    target = data_dir / "papers" / paper_id / "source" / "source.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    PaperRepository(data_dir).create_or_get(
        PaperRecord(
            paper_id=paper_id,
            source_pdf_path="source/source.pdf",
            size_bytes=target.stat().st_size,
        )
    )
    return paper_id


def _pdf_bytes(tmp_path: Path) -> bytes:
    source = tmp_path / "upload.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Uploaded PeerAssist Study\nWe show recall improves.")
    document.save(source)
    document.close()
    return source.read_bytes()


def _json(url: str, *, method: str = "GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Connection": "close"},
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def test_review_job_http_lifecycle_and_sse_replay(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PEERASSIST_MODEL_SETTINGS_PATH", str(tmp_path / "model-settings.json"))
    monkeypatch.delenv("PEERASSIST_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    paper_id = _paper(tmp_path)
    server = create_review_job_server(data_dir=tmp_path, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = _json(f"{base}/api/health")
        assert status == 200
        assert health["status"] == "ok"
        status, created = _json(
            f"{base}/api/reviews",
            method="POST",
            payload={"paper_id": paper_id, "mode": "fast", "idempotency_key": "api-test"},
        )
        assert status == 202
        job_id = created["job"]["id"]
        _, listed = _json(f"{base}/api/jobs")
        assert any(job["id"] == job_id for job in listed["jobs"])

        for _ in range(100):
            _, snapshot = _json(f"{base}/api/jobs/{job_id}")
            if snapshot["job"]["status"] == "blocked":
                break
            sleep(0.02)
        assert snapshot["job"]["resume_stage"] == "agents"

        _, listed = _json(f"{base}/api/jobs")
        listed_job = next(job for job in listed["jobs"] if job["id"] == job_id)
        timeline = listed_job["timeline"]
        assert timeline["event_count"] >= 10
        assert timeline["last_event_id"] == timeline["items"][-1]["event_id"]
        completed = [item for item in timeline["items"] if item["event_type"] == "stage_completed"]
        assert completed[0]["stage"] == "validate"
        assert completed[0]["duration_ms"] >= 0
        assert all("payload" not in item for item in timeline["items"])

        request = Request(
            f"{base}/api/jobs/{job_id}/events",
            headers={"Last-Event-ID": "0", "Connection": "close"},
        )
        with urlopen(request, timeout=5) as response:
            events = response.read().decode()
        assert "event: stage_started" in events
        assert "event: heartbeat" in events

        _, granted = _json(
            f"{base}/api/jobs/{job_id}/consents/model",
            method="POST",
            payload={"decision": "granted", "actor": "reviewer"},
        )
        assert granted["job"]["status"] == "queued"
        for _ in range(100):
            _, snapshot = _json(f"{base}/api/jobs/{job_id}")
            if snapshot["job"]["status"] == "awaiting_human_confirmation":
                break
            sleep(0.02)
        assert snapshot["job"]["status"] == "awaiting_human_confirmation"

        _, cancellable = _json(
            f"{base}/api/reviews",
            method="POST",
            payload={"paper_id": paper_id, "mode": "fast", "idempotency_key": "api-cancel"},
        )
        cancellable_id = cancellable["job"]["id"]
        for _ in range(100):
            _, cancellable_snapshot = _json(f"{base}/api/jobs/{cancellable_id}")
            if cancellable_snapshot["job"]["status"] == "blocked":
                break
            sleep(0.02)
        _, requested = _json(f"{base}/api/jobs/{cancellable_id}/cancel", method="POST", payload={})
        assert requested["job"]["status"] == "cancel_requested"
        for _ in range(100):
            _, cancelled = _json(f"{base}/api/jobs/{cancellable_id}")
            if cancelled["job"]["status"] == "cancelled":
                break
            sleep(0.02)
        assert cancelled["job"]["status"] == "cancelled"
        assert cancelled["job"]["timeline"]["items"][-1]["event_type"] == "job_cancelled"

        _, retried = _json(f"{base}/api/jobs/{cancellable_id}/retry", method="POST", payload={})
        assert retried["job"]["status"] == "queued"
        for _ in range(100):
            _, retry_snapshot = _json(f"{base}/api/jobs/{cancellable_id}")
            if retry_snapshot["job"]["status"] == "blocked":
                break
            sleep(0.02)
        retry_items = retry_snapshot["job"]["timeline"]["items"]
        assert any(item["event_type"] == "job_retried" and item["attempt"] == 2 for item in retry_items)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_multipart_upload_creates_paper_and_review_job(tmp_path: Path) -> None:
    content = _pdf_bytes(tmp_path)
    boundary = "peerassist-http-upload"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="paper.pdf"\r\n',
            b"Content-Type: application/pdf\r\n\r\n",
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    server = create_review_job_server(data_dir=tmp_path, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/papers/upload",
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Connection": "close",
            },
        )
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
        expected = hashlib.sha256(content).hexdigest()
        assert payload["paper"]["paper_id"] == expected
        assert payload["job"]["paper_id"] == expected
        assert (tmp_path / "papers" / expected / "source" / "source.pdf").read_bytes() == content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_paper_source_supports_byte_ranges(tmp_path: Path) -> None:
    paper_id = _paper(tmp_path)
    content = (tmp_path / "paper.pdf").read_bytes()
    server = create_review_job_server(data_dir=tmp_path, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/papers/{paper_id}/source",
            headers={"Range": "bytes=0-31", "Connection": "close"},
        )
        with urlopen(request, timeout=5) as response:
            assert response.status == 206
            assert response.headers["Accept-Ranges"] == "bytes"
            assert response.headers["Content-Range"] == f"bytes 0-31/{len(content)}"
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.read() == content[:32]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_job_workspace_applies_confirmation_decisions(tmp_path: Path) -> None:
    paper_id = _paper(tmp_path)
    job_id = uuid4()
    state = ReviewJobRepository(tmp_path).create(
        ReviewJobState(
            id=job_id,
            paper_id=paper_id,
            run_dir=f"jobs/{job_id}/run",
            attempt_id="attempt-workspace-test",
            stage="await_confirmation",
            status="awaiting_human_confirmation",
        )
    )
    concern = Concern(
        id="concern-workspace-test",
        level=ConcernLevel.CLARIFICATION_NEEDED,
        category="statistics",
        title="Sample size needs clarification",
        evidence_ids=["P01-L001"],
        impact="The reported result may be underpowered.",
        author_action="Please report the sample-size rationale.",
        metadata={"citation_finding_ids": ["CF-1"]},
    )
    out_dir = peerassist_stage_dir(tmp_path / state.run_dir)
    bundle = build_confirmation_bundle(
        concerns=[concern],
        evidence_lookup={"P01-L001": "page 1, line 1"},
    )
    write_json_file(
        out_dir / "peerassist_concerns.json",
        {"paper_id": paper_id, "concerns": [concern.model_dump(mode="json")]},
    )
    write_json_file(out_dir / "confirmation_bundle.json", bundle)
    write_json_file(out_dir / "confirmation_review_queue.json", build_confirmation_review_queue(bundle))
    write_json_file(
        out_dir / "human_confirmations.json",
        {"schema_version": "peerassist.human_confirmations.v2", "revision": 0, "mutation_revision": 0, "actions": []},
    )
    write_json_file(
        out_dir / "evidence_ledger.json",
        {
            "items": [
                {
                    "id": "P01-L001",
                    "type": "text_span",
                    "page": 1,
                    "locator": "page 1, line 1",
                    "text": "We evaluate ten samples.",
                    "section": "Experiments",
                    "bbox": [72, 72, 240, 90],
                },
                {
                    "id": "M-1",
                    "type": "citation",
                    "page": 1,
                    "locator": "page 1, citation [1]",
                    "text": "[1]",
                    "section": "Introduction",
                    "bbox": [250, 72, 270, 90],
                },
                {
                    "id": "R-E-1",
                    "type": "reference",
                    "page": 2,
                    "locator": "page 2, reference 1",
                    "text": "[1] Alpha Study. 2024.",
                    "section": "References",
                    "bbox": [72, 120, 360, 140],
                },
            ]
        },
    )
    write_json_file(
        out_dir / "citation_audit.json",
        {
            "schema_version": "peerassist.citation_audit.v1",
            "paper_id": paper_id,
            "parse_version": "test-v1",
            "records": [
                {
                    "id": "R-1",
                    "reference_number": 1,
                    "source_evidence_ids": ["R-E-1"],
                    "raw_text": "[1] Alpha Study. 2024.",
                    "title": "Alpha Study",
                    "doi": "10.1000/alpha",
                    "year": 2024,
                    "parse_confidence": 0.98,
                }
            ],
            "links": [
                {
                    "id": "L-1",
                    "mention_evidence_id": "M-1",
                    "reference_number": 1,
                    "status": "linked",
                    "reference_record_ids": ["R-1"],
                    "reference_evidence_ids": ["R-E-1"],
                }
            ],
            "verifications": [
                {
                    "id": "V-1",
                    "reference_record_id": "R-1",
                    "source": "crossref",
                    "status": "completed",
                    "adapter": {"name": "crossref", "version": "1"},
                    "query": {"doi": "10.1000/alpha"},
                    "attempt_id": "attempt-citation-test",
                    "attempt_number": 1,
                    "checked_at": "2026-07-14T01:30:00Z",
                    "tool_call_id": "call-citation-test",
                    "match": {
                        "method": "doi_exact",
                        "candidate_count": 1,
                        "selected_candidate_id": "external-1",
                        "selection_reason": "exact DOI",
                        "candidate_ids": ["external-1"],
                    },
                    "source_record": {"id": "external-1", "url": "https://example.invalid/alpha"},
                    "raw_response_artifact": {"path": "private/raw.json", "sha256": "0" * 64},
                    "observed_metadata": {"title": "Alpha Study Revised", "year": 2024},
                    "field_differences": [
                        {
                            "field": "title",
                            "manuscript_value": "Alpha Study",
                            "external_value": "Alpha Study Revised",
                            "normalized_manuscript_value": "alpha study",
                            "normalized_external_value": "alpha study revised",
                            "comparison": "mismatch",
                            "rule": "normalized_exact",
                        }
                    ],
                }
            ],
            "findings": [
                {
                    "id": "CF-1",
                    "status": "metadata_mismatch",
                    "severity": "clarification_needed",
                    "citation_link_ids": ["L-1"],
                    "reference_record_ids": ["R-1"],
                    "mention_evidence_ids": ["M-1"],
                    "reference_evidence_ids": ["R-E-1"],
                    "verification_ids": ["V-1"],
                    "message": "Reference title differs from the external source.",
                    "requires_human_review": True,
                    "metadata": {},
                }
            ],
            "coverage": {"records": 1, "links": 1, "verifications": 1, "findings": 1},
            "warnings": [],
        },
    )
    write_json_file(out_dir / "agent_results.json", {"results": []})
    write_json_file(out_dir / "capability_invocations.json", {"results": []})

    server = create_review_job_server(data_dir=tmp_path, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, workspace = _json(f"{base}/api/jobs/{job_id}/workspace")
        assert status == 200
        item = workspace["state"]["queue"]["items"][0]
        assert item["evidence"][0]["page"] == 1
        assert workspace["state"]["pending_count"] == 1
        citation = workspace["state"]["citation_audit"]
        assert citation["available"] is True
        assert citation["links"][0]["mention"]["page"] == 1
        assert citation["links"][0]["verification"]["field_differences"][0]["field"] == "title"
        assert citation["links"][0]["concern_id"] == concern.id

        status, decision = _json(
            f"{base}/api/jobs/{job_id}/decisions",
            method="POST",
            payload={
                "concern_id": concern.id,
                "finding_lineage_id": concern.finding_lineage_id,
                "finding_id": concern.finding_id,
                "finding_revision": concern.revision,
                "confirmation_revision": 0,
                "action": "confirm",
                "reviewer_id": "api-reviewer",
                "timestamp": "2026-07-14T01:30:00Z",
            },
        )
        assert status == 200
        assert decision["job"]["confirmation_revision"] == 1
        assert decision["state"]["pending_count"] == 0
        assert decision["state"]["queue"]["items"][0]["status"] == "confirmed"

        status, finalized = _json(
            f"{base}/api/jobs/{job_id}/finalize",
            method="POST",
            payload={"confirmation_revision": 1},
        )
        assert status == 200
        assert finalized["job"]["status"] == "completed"

        _, completed_workspace = _json(f"{base}/api/jobs/{job_id}/workspace")
        artifacts = completed_workspace["state"]["artifacts"]
        assert artifacts["ready"] is True
        assert {item["name"] for item in artifacts["items"]} == {
            "report_en_md",
            "report_zh_md",
            "report_en_json",
            "report_zh_json",
        }
        zh_report = next(item for item in artifacts["items"] if item["name"] == "report_zh_md")
        with urlopen(f"{base}{zh_report['download_url']}", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/markdown")
            assert "PeerAssist" in response.read().decode("utf-8")
        with urlopen(f"{base}{zh_report['download_url']}?disposition=inline", timeout=5) as response:
            assert response.headers["Content-Disposition"].startswith("inline;")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
