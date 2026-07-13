from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Thread
from time import sleep
from urllib.request import Request, urlopen

import pymupdf

from peerassist.confirmation_server import create_review_job_server
from peerassist.job_repository import PaperRepository
from schemas.peerassist_jobs import PaperRecord


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


def test_review_job_http_lifecycle_and_sse_replay(tmp_path: Path) -> None:
    paper_id = _paper(tmp_path)
    server = create_review_job_server(data_dir=tmp_path, host="127.0.0.1", port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, created = _json(
            f"{base}/api/reviews",
            method="POST",
            payload={"paper_id": paper_id, "mode": "fast", "idempotency_key": "api-test"},
        )
        assert status == 202
        job_id = created["job"]["id"]

        for _ in range(100):
            _, snapshot = _json(f"{base}/api/jobs/{job_id}")
            if snapshot["job"]["status"] == "blocked":
                break
            sleep(0.02)
        assert snapshot["job"]["resume_stage"] == "agents"

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
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
