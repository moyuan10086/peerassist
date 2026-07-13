"""Thin HTTP API over durable PeerAssist papers and review jobs."""

from __future__ import annotations

import argparse
import json
import re
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from peerassist.job_adapters import build_local_stage_adapters
from peerassist.job_repository import PaperRepository, ReviewJobRepository
from peerassist.job_runner import RecoverableReviewJobRunner, ReviewJobScheduler
from peerassist.upload import (
    UploadInterruptedError,
    UploadParseError,
    UploadTooLargeError,
    UploadValidationError,
    persist_multipart_upload,
)
from schemas.peerassist_jobs import ReviewJobState

_JOB_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)(?P<action>/events|/cancel|/retry|/finalize)?$")
_CONSENT_RE = re.compile(
    r"^/api/jobs/(?P<job>[0-9a-f-]+)/consents/(?P<service>parse|search|model)$"
)
_PAPER_SOURCE_RE = re.compile(r"^/api/papers/(?P<paper>[0-9a-f]{64})/source$")


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

    def paper_source(self, paper_id: str) -> tuple[Path, str]:
        record = self.paper_repository.get(paper_id)
        paper_root = (self.paper_repository.papers_dir / record.paper_id).resolve(strict=True)
        source = (paper_root / record.source_pdf_path).resolve(strict=True)
        source.relative_to(paper_root)
        if not source.is_file():
            raise FileNotFoundError("paper source is not a regular file")
        return source, record.paper_id


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
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            self._send_json({"status": "ok", "service": "peerassist-review-jobs"})
            return
        if path == "/api/jobs":
            self._send_json(
                {
                    "jobs": [
                        state.model_dump(mode="json")
                        for state in self.service.repository.list()
                    ]
                }
            )
            return
        paper_match = _PAPER_SOURCE_RE.match(path)
        if paper_match is not None:
            self._send_paper_source(paper_match.group("paper"))
            return
        match = _JOB_RE.match(path)
        if match is None:
            self._send_json({"error": "not_found"}, status=404)
            return
        job_id = UUID(match.group("job"))
        if match.group("action") == "/events":
            self._send_events(job_id)
            return
        self._send_json({"job": self._job(job_id)})

    def do_HEAD(self) -> None:
        match = _PAPER_SOURCE_RE.match(self.path.split("?", 1)[0])
        if match is None:
            self._send_json({"error": "not_found"}, status=404, head_only=True)
            return
        self._send_paper_source(match.group("paper"), head_only=True)

    def do_POST(self) -> None:
        if self.path == "/api/papers/upload":
            self._upload_paper()
            return
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

    def _upload_paper(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send_json({"error": "length_required"}, status=411)
            return
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json({"error": "invalid_request_framing"}, status=400)
            return
        if length < 0:
            self._send_json({"error": "invalid_request_framing"}, status=400)
            return

        def chunks():
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

        try:
            record = persist_multipart_upload(
                chunks(),
                content_type=str(self.headers.get("Content-Type") or ""),
                content_length=length,
                repository=self.service.paper_repository,
            )
            state = self.service.create_review(
                paper_id=record.paper_id,
                mode="fast",
                idempotency_key=f"upload:{record.paper_id}",
            )
        except UploadTooLargeError as exc:
            self._send_json({"error": str(exc)}, status=413)
            return
        except (UploadValidationError, UploadParseError, UploadInterruptedError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=409)
            return
        self._send_json(
            {
                "paper": record.model_dump(mode="json"),
                "job": state.model_dump(mode="json"),
            },
            status=202,
        )

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

    def _send_paper_source(self, paper_id: str, *, head_only: bool = False) -> None:
        try:
            path, digest = self.service.paper_source(paper_id)
            stat = path.stat()
        except (FileNotFoundError, ValueError):
            self._send_json({"error": "paper_source_not_found"}, status=404, head_only=head_only)
            return

        size = stat.st_size
        etag = f'"sha256-{digest}"'
        if self.headers.get("If-None-Match") == etag and not self.headers.get("Range"):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, max-age=3600")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.close_connection = True
            return

        start = 0
        end = max(0, size - 1)
        status = 200
        range_header = self.headers.get("Range", "").strip()
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            if not match or not size:
                self._send_unsatisfied_range(size)
                return
            raw_start, raw_end = match.groups()
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else end
            elif raw_end:
                suffix_length = int(raw_end)
                if suffix_length <= 0:
                    self._send_unsatisfied_range(size)
                    return
                start = max(0, size - suffix_length)
            else:
                self._send_unsatisfied_range(size)
                return
            if start >= size or end < start:
                self._send_unsatisfied_range(size)
                return
            end = min(end, size - 1)
            status = 206

        content_length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'inline; filename="paper.pdf"')
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", formatdate(stat.st_mtime, usegmt=True))
        self.send_header("Connection", "close")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(content_length))
        self.end_headers()
        if not head_only:
            with path.open("rb") as pdf_file:
                pdf_file.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = pdf_file.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        self.close_connection = True

    def _send_unsatisfied_range(self, size: int) -> None:
        self.send_response(416)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.close_connection = True

    def _send_json(
        self, payload: dict[str, Any], *, status: int = 200, head_only: bool = False
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
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
