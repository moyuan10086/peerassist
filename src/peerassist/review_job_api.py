"""Thin HTTP API over durable PeerAssist papers and review jobs."""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from peerassist.job_adapters import build_local_stage_adapters
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.job_runner import RecoverableReviewJobRunner, ReviewJobScheduler
from schemas.peerassist_jobs import ReviewJobState

_JOB_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)(?P<action>/events|/cancel|/retry|/finalize)?$")
_CONSENT_RE = re.compile(
    r"^/api/jobs/(?P<job>[0-9a-f-]+)/consents/(?P<service>parse|search|model)$"
)


class ReviewJobService:
    def __init__(self, data_dir: Path) -> None:
        self.paper_repository = PaperRepository(data_dir)
        self.repository = ReviewJobRepository(data_dir)
        self.runner = RecoverableReviewJobRunner(
            self.repository,
            build_local_stage_adapters(self.repository),
            owner=f"api-{uuid4().hex}",
        )
        self.scheduler = ReviewJobScheduler(self.runner, max_workers=2)
        self.scheduler.recover_orphans()

    def create_review(
        self,
        *,
        paper_id: str,
        mode: str,
        idempotency_key: str,
    ) -> ReviewJobState:
        self.paper_repository.get(paper_id)
        if mode != "fast":
            raise ValueError("mode_unavailable")
        if idempotency_key:
            for existing in self.repository.list():
                if (
                    existing.paper_id == paper_id
                    and existing.metadata.get("idempotency_key") == idempotency_key
                ):
                    return existing
        job_id = uuid4()
        state = self.repository.create(
            ReviewJobState(
                id=job_id,
                paper_id=paper_id,
                run_dir=f"jobs/{job_id}/run",
                attempt_id=f"attempt-{uuid4().hex}",
                metadata={"idempotency_key": idempotency_key},
            )
        )
        self.scheduler.submit(state.id)
        return state

    def close(self) -> None:
        self.scheduler.shutdown(wait=True)


class ReviewJobHttpServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], data_dir: Path) -> None:
        self.review_service = ReviewJobService(data_dir)
        super().__init__(address, _ReviewJobHandler)

    def server_close(self) -> None:
        self.review_service.close()
        super().server_close()


class _ReviewJobHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> ReviewJobService:
        return self.server.review_service  # type: ignore[attr-defined,no-any-return]

    def do_GET(self) -> None:
        match = _JOB_RE.match(self.path)
        if match is None:
            self._send_json({"error": "not_found"}, status=404)
            return
        job_id = UUID(match.group("job"))
        if match.group("action") == "/events":
            self._send_events(job_id)
            return
        self._send_json({"job": self._job(job_id)})

    def do_POST(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            code = str(exc)
            self._send_json({"error": code}, status=411 if code == "length_required" else 400)
            return
        if self.path == "/api/reviews":
            try:
                state = self.service.create_review(
                    paper_id=str(payload.get("paper_id") or ""),
                    mode=str(payload.get("mode") or "fast"),
                    idempotency_key=str(payload.get("idempotency_key") or ""),
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=409)
                return
            self._send_json({"job": state.model_dump(mode="json")}, status=202)
            return
        consent = _CONSENT_RE.match(self.path)
        if consent is not None:
            if str(payload.get("decision") or "") != "granted":
                self._send_json({"error": "unsupported_consent_decision"}, status=400)
                return
            state = self.service.runner.grant_consent(
                consent.group("job"),
                service=consent.group("service"),
                actor=str(payload.get("actor") or "reviewer"),
            )
            self.service.scheduler.submit(state.id)
            self._send_json({"job": state.model_dump(mode="json")})
            return
        match = _JOB_RE.match(self.path)
        if match is None:
            self._send_json({"error": "not_found"}, status=404)
            return
        job_id = match.group("job")
        action = match.group("action")
        if action == "/cancel":
            state = self.service.runner.request_cancel(job_id)
        elif action == "/retry":
            state = self.service.runner.retry(job_id)
            self.service.scheduler.submit(state.id)
        elif action == "/finalize":
            state = self.service.runner.finalize(
                job_id,
                expected_confirmation_revision=int(
                    payload.get("confirmation_revision") or 0
                ),
                override_reason=str(payload.get("override_reason") or ""),
            )
        else:
            self._send_json({"error": "not_found"}, status=404)
            return
        self._send_json({"job": state.model_dump(mode="json")})

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("length_required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("invalid_request_framing") from exc
        if length < 0 or length > 1024 * 1024:
            raise ValueError("invalid_request_framing")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid_json") from exc
        if not isinstance(payload, dict):
            raise ValueError("invalid_json")
        return payload

    def _job(self, job_id: UUID) -> dict[str, Any]:
        return self.service.repository.get(job_id).model_dump(mode="json")

    def _send_events(self, job_id: UUID) -> None:
        try:
            after = int(self.headers.get("Last-Event-ID") or "0")
        except ValueError:
            after = 0
        events = self.service.repository.replay_events(job_id, after_event_id=max(0, after))
        frames = [
            f"id: {event.event_id}\nevent: {event.event_type}\n"
            f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            for event in events
        ]
        frames.append("event: heartbeat\ndata: {}\n\n")
        body = "".join(frames).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def create_review_job_server(
    *, data_dir: Path, host: str = "127.0.0.1", port: int = 0
) -> ReviewJobHttpServer:
    return ReviewJobHttpServer((host, port), Path(data_dir))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the PeerAssist Review Job API.")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args(argv)
    server = create_review_job_server(
        data_dir=Path(args.data_dir),
        host=args.host,
        port=args.port,
    )
    print(
        json.dumps(
            {"url": f"http://{args.host}:{server.server_address[1]}", "data_dir": args.data_dir},
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
