"""Thin HTTP API over durable PeerAssist papers and review jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from common.pipeline_context import peerassist_stage_dir, read_json_file
from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state
from peerassist.confirmations import (
    apply_confirmations,
    build_confirmation_bundle,
    build_confirmation_review_queue,
)
from peerassist.job_adapters import build_local_stage_adapters
from peerassist.job_repository import (
    PaperRepository,
    RepositoryConflictError,
    ReviewJobRepository,
)
from peerassist.job_runner import RecoverableReviewJobRunner, ReviewJobScheduler
from peerassist.upload import (
    UploadInterruptedError,
    UploadParseError,
    UploadTooLargeError,
    UploadValidationError,
    persist_multipart_upload,
)
from schemas.citation import CitationAudit
from schemas.peerassist import Concern, HumanConfirmationAction
from schemas.peerassist_jobs import ReviewJobState, ReviewJobStatus, ReviewStage

_JOB_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)(?P<action>/events|/cancel|/retry|/finalize)?$")
_CONSENT_RE = re.compile(
    r"^/api/jobs/(?P<job>[0-9a-f-]+)/consents/(?P<service>parse|search|model)$"
)
_PAPER_SOURCE_RE = re.compile(r"^/api/papers/(?P<paper>[0-9a-f]{64})/source$")
_JOB_WORKSPACE_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)/workspace$")
_JOB_DECISION_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)/decisions$")
_JOB_ARTIFACTS_RE = re.compile(r"^/api/jobs/(?P<job>[0-9a-f-]+)/artifacts$")
_JOB_ARTIFACT_RE = re.compile(
    r"^/api/jobs/(?P<job>[0-9a-f-]+)/artifacts/(?P<artifact>[a-z0-9_]+)$"
)
_REPORT_ARTIFACT_NAMES = {
    "report_en_md",
    "report_zh_md",
    "report_en_json",
    "report_zh_json",
}
_REVIEW_STAGE_SEQUENCE = list(ReviewStage)
_REVIEW_STAGE_PREDECESSOR = {
    stage: _REVIEW_STAGE_SEQUENCE[index - 1]
    for index, stage in enumerate(_REVIEW_STAGE_SEQUENCE)
    if index > 0
}


class DecisionCommitInterruptedError(RuntimeError):
    """A decision persisted but its aggregate state or audit event needs reconciliation."""


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
        self._decision_reconciliation_lock = Lock()
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

    def job_view(self, state: ReviewJobState) -> dict[str, Any]:
        state = self._reconcile_confirmation_commit(state)
        events = self.repository.replay_events(state.id)
        attempts: dict[str, int] = {}
        stage_starts: dict[tuple[str, ReviewStage], Any] = {}
        items: list[dict[str, Any]] = []
        terminal_stage_events = {"stage_completed", "stage_failed"}
        for event in events:
            attempt_key = event.attempt_id or "unknown"
            if attempt_key not in attempts:
                attempts[attempt_key] = len(attempts) + 1
            event_stage = event.stage
            if event.event_type == "stage_completed" and event_stage is not None:
                current_key = (attempt_key, event_stage)
                if current_key not in stage_starts:
                    predecessor = _REVIEW_STAGE_PREDECESSOR.get(event_stage)
                    if predecessor is not None and (attempt_key, predecessor) in stage_starts:
                        event_stage = predecessor
            duration_ms: int | None = None
            if event.event_type == "stage_started" and event_stage is not None:
                stage_starts[(attempt_key, event_stage)] = event.timestamp
            elif event.event_type in terminal_stage_events and event_stage is not None:
                started_at = stage_starts.get((attempt_key, event_stage))
                if started_at is not None:
                    duration_ms = max(
                        0,
                        round((event.timestamp - started_at).total_seconds() * 1000),
                    )
            details: dict[str, Any] = {}
            service = event.payload.get("service")
            if isinstance(service, str) and service in {"parse", "search", "model"}:
                details["service"] = service
            items.append(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "timestamp": event.timestamp.isoformat(),
                    "stage": event_stage.value if event_stage is not None else None,
                    "status": event.status.value if event.status is not None else None,
                    "attempt": attempts[attempt_key],
                    "duration_ms": duration_ms,
                    "details": details,
                }
            )
        payload = state.model_dump(mode="json")
        payload["timeline"] = {
            "event_count": len(events),
            "last_event_id": events[-1].event_id if events else 0,
            "items": items[-24:],
        }
        return payload

    def paper_source(self, paper_id: str) -> tuple[Path, str]:
        record = self.paper_repository.get(paper_id)
        paper_root = (self.paper_repository.papers_dir / record.paper_id).resolve(strict=True)
        source = (paper_root / record.source_pdf_path).resolve(strict=True)
        source.relative_to(paper_root)
        if not source.is_file():
            raise FileNotFoundError("paper source is not a regular file")
        return source, record.paper_id

    def workspace(self, job_id: UUID | str) -> tuple[ReviewJobState, dict[str, Any]]:
        state = self._reconcile_confirmation_commit(self.repository.get(job_id))
        run_dir = self._run_dir(state)
        workspace = load_confirmation_state(run_dir=run_dir)
        out_dir = peerassist_stage_dir(run_dir)
        ledger_payload = read_json_file(out_dir / "evidence_ledger.json")
        if not ledger_payload:
            ledger_payload = self._stage_result(state, ReviewStage.EVIDENCE)
        evidence_rows = ledger_payload.get("items") if isinstance(ledger_payload.get("items"), list) else []
        evidence_by_id = {
            str(row.get("id") or ""): row
            for row in evidence_rows
            if isinstance(row, dict) and row.get("id")
        }
        evidence_lookup = {
            evidence_id: str(row.get("locator") or evidence_id)
            for evidence_id, row in evidence_by_id.items()
        }

        concerns_payload = read_json_file(out_dir / "peerassist_concerns.json")
        concern_rows = (
            concerns_payload.get("concerns")
            if isinstance(concerns_payload.get("concerns"), list)
            else []
        )
        concerns = [Concern.model_validate(row) for row in concern_rows if isinstance(row, dict)]
        confirmations = read_json_file(out_dir / "human_confirmations.json")
        action_rows = (
            confirmations.get("actions") if isinstance(confirmations.get("actions"), list) else []
        )
        actions = [
            HumanConfirmationAction.model_validate(row)
            for row in action_rows
            if isinstance(row, dict)
        ]
        effective_concerns = apply_confirmations(concerns, actions)
        bundle = build_confirmation_bundle(
            concerns=effective_concerns,
            evidence_lookup=evidence_lookup,
        )
        queue = build_confirmation_review_queue(bundle)
        for item in queue.get("items", []):
            if not isinstance(item, dict):
                continue
            item["evidence"] = [
                {
                    "id": evidence_id,
                    "locator": str(evidence_by_id.get(evidence_id, {}).get("locator") or evidence_id),
                    "page": evidence_by_id.get(evidence_id, {}).get("page"),
                    "text": str(evidence_by_id.get(evidence_id, {}).get("text") or ""),
                    "bbox": evidence_by_id.get(evidence_id, {}).get("bbox"),
                }
                for evidence_id in item.get("evidence_ids", [])
            ]

        workspace["queue"] = queue
        workspace["actions"] = [action.model_dump(mode="json") for action in actions]
        workspace["actions_count"] = len(actions)
        workspace["confirmation_revision"] = max(
            0, int(confirmations.get("revision") or 0)
        )
        workspace["pending_count"] = sum(
            1
            for concern in effective_concerns
            if concern.status.value == "pending_human_confirmation"
        )
        workspace["evidence_preview"] = [
            {
                "id": evidence_id,
                "locator": str(row.get("locator") or ""),
                "page": row.get("page"),
                "text": str(row.get("text") or ""),
            }
            for evidence_id, row in list(evidence_by_id.items())[:8]
        ]
        workspace.pop("paths", None)
        runtime = workspace.get("runtime")
        if isinstance(runtime, dict):
            runtime.pop("stage_dir", None)
            runtime["queue_items"] = len(queue.get("items", []))
        workspace["ready_for_confirmation"] = (
            state.status is ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION
        )
        workspace["citation_audit"] = self._citation_summary(
            state,
            out_dir=out_dir,
            evidence_by_id=evidence_by_id,
            queue_items=queue.get("items", []),
        )
        workspace["artifacts"] = self.artifact_index(state.id, state=state)
        return state, workspace

    def _citation_summary(
        self,
        state: ReviewJobState,
        *,
        out_dir: Path,
        evidence_by_id: dict[str, dict[str, Any]],
        queue_items: list[Any],
    ) -> dict[str, Any]:
        payload = read_json_file(out_dir / "citation_audit.json")
        if not payload:
            stage_payload = self._stage_result(state, ReviewStage.CITATION)
            payload = stage_payload.get("audit") if isinstance(stage_payload.get("audit"), dict) else {}
        if not payload:
            return {"available": False, "records": [], "links": [], "warnings": []}
        audit = CitationAudit.model_validate(payload)
        records_by_id = {record.id: record for record in audit.records}
        verifications_by_record: dict[str, list[Any]] = {}
        for verification in audit.verifications:
            verifications_by_record.setdefault(verification.reference_record_id, []).append(
                verification
            )
        findings_by_link: dict[str, list[Any]] = {}
        for finding in audit.findings:
            for link_id in finding.citation_link_ids:
                findings_by_link.setdefault(link_id, []).append(finding)
        concerns_by_finding: dict[str, dict[str, Any]] = {}
        for item in queue_items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            finding_ids = (
                metadata.get("citation_finding_ids")
                if isinstance(metadata.get("citation_finding_ids"), list)
                else []
            )
            for finding_id in finding_ids:
                concerns_by_finding[str(finding_id)] = item

        def evidence_view(evidence_id: str) -> dict[str, Any]:
            row = evidence_by_id.get(evidence_id, {})
            return {
                "id": evidence_id,
                "page": row.get("page"),
                "locator": str(row.get("locator") or evidence_id),
                "text": str(row.get("text") or ""),
                "bbox": row.get("bbox"),
            }

        def verification_view(reference_id: str) -> dict[str, Any] | None:
            rows = sorted(
                verifications_by_record.get(reference_id, []),
                key=lambda item: (item.attempt_number, item.checked_at),
            )
            if not rows:
                return None
            verification = rows[-1]
            return {
                "id": verification.id,
                "source": verification.source,
                "status": verification.status.value,
                "adapter": {
                    "name": verification.adapter.name,
                    "version": verification.adapter.version,
                },
                "checked_at": verification.checked_at,
                "attempt_number": verification.attempt_number,
                "source_url": verification.source_record.url if verification.source_record else "",
                "observed_metadata": dict(verification.observed_metadata),
                "field_differences": [
                    difference.model_dump(mode="json")
                    for difference in verification.field_differences
                ],
                "error_code": verification.error_code,
            }

        records = [
            {
                "id": record.id,
                "reference_number": record.reference_number,
                "raw_text": record.raw_text,
                "title": record.title,
                "doi": record.doi,
                "year": record.year,
                "parse_confidence": record.parse_confidence,
                "evidence": [evidence_view(item) for item in record.source_evidence_ids],
                "verification": verification_view(record.id),
            }
            for record in audit.records
        ]
        links: list[dict[str, Any]] = []
        for link in audit.links:
            findings = findings_by_link.get(link.id, [])
            linked_concern = next(
                (
                    concerns_by_finding.get(finding.id)
                    for finding in findings
                    if concerns_by_finding.get(finding.id) is not None
                ),
                None,
            )
            references = [
                records_by_id[record_id]
                for record_id in link.reference_record_ids
                if record_id in records_by_id
            ]
            links.append(
                {
                    "id": link.id,
                    "reference_number": link.reference_number,
                    "status": link.status.value,
                    "mention": evidence_view(link.mention_evidence_id),
                    "references": [
                        {
                            "id": record.id,
                            "raw_text": record.raw_text,
                            "title": record.title,
                            "doi": record.doi,
                            "year": record.year,
                            "evidence": [
                                evidence_view(item) for item in record.source_evidence_ids
                            ],
                        }
                        for record in references
                    ],
                    "verification": (
                        verification_view(references[0].id) if references else None
                    ),
                    "findings": [
                        {
                            "id": finding.id,
                            "status": finding.status.value,
                            "severity": finding.severity.value,
                            "message": finding.message,
                            "requires_human_review": finding.requires_human_review,
                        }
                        for finding in findings
                    ],
                    "concern_id": str(linked_concern.get("id") or "")
                    if linked_concern
                    else "",
                    "concern_status": str(linked_concern.get("status") or "")
                    if linked_concern
                    else "",
                }
            )
        return {
            "available": True,
            "parse_version": audit.parse_version,
            "coverage": dict(audit.coverage),
            "records": records,
            "links": links,
            "warnings": list(audit.warnings),
        }

    def artifact_index(
        self, job_id: UUID | str, *, state: ReviewJobState | None = None
    ) -> dict[str, Any]:
        current = state or self.repository.get(job_id)
        manifest_entry = self._report_manifest(current)
        if manifest_entry is None:
            return {"ready": False, "items": []}
        _out_dir, manifest = manifest_entry
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        hashes = (
            manifest.get("artifact_sha256")
            if isinstance(manifest.get("artifact_sha256"), dict)
            else {}
        )
        items: list[dict[str, Any]] = []
        for name in sorted(_REPORT_ARTIFACT_NAMES):
            relative_path = str(artifacts.get(name) or "")
            if not relative_path:
                continue
            path = self._report_artifact_path(current, relative_path)
            media_type = "text/markdown; charset=utf-8" if path.suffix == ".md" else "application/json; charset=utf-8"
            items.append(
                {
                    "name": name,
                    "filename": path.name,
                    "media_type": media_type,
                    "size_bytes": path.stat().st_size,
                    "sha256": str(hashes.get(name) or ""),
                    "download_url": f"/api/jobs/{current.id}/artifacts/{name}",
                }
            )
        return {
            "ready": len(items) == len(_REPORT_ARTIFACT_NAMES),
            "report_version": str(manifest.get("report_version") or ""),
            "confirmation_revision": int(manifest.get("confirmation_revision") or 0),
            "items": items,
        }

    def artifact_file(self, job_id: UUID | str, name: str) -> tuple[Path, str]:
        if name not in _REPORT_ARTIFACT_NAMES:
            raise FileNotFoundError("unknown report artifact")
        state = self.repository.get(job_id)
        manifest_entry = self._report_manifest(state)
        if manifest_entry is None:
            raise FileNotFoundError("final report is not available")
        _out_dir, manifest = manifest_entry
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
        hashes = (
            manifest.get("artifact_sha256")
            if isinstance(manifest.get("artifact_sha256"), dict)
            else {}
        )
        path = self._report_artifact_path(state, str(artifacts.get(name) or ""))
        expected_hash = str(hashes.get(name) or "")
        if expected_hash and hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("report artifact hash mismatch")
        media_type = "text/markdown; charset=utf-8" if path.suffix == ".md" else "application/json; charset=utf-8"
        return path, media_type

    def _report_manifest(self, state: ReviewJobState) -> tuple[Path, dict[str, Any]] | None:
        out_dir = peerassist_stage_dir(self._run_dir(state))
        pointer = read_json_file(out_dir / "current_final_report.json")
        relative_manifest = str(pointer.get("manifest_path") or "")
        if not relative_manifest:
            return None
        reports_root = (out_dir / "reports").resolve(strict=True)
        manifest_path = (out_dir / relative_manifest).resolve(strict=True)
        manifest_path.relative_to(reports_root)
        if manifest_path.name != "manifest.json" or not manifest_path.is_file():
            raise ValueError("invalid final report manifest")
        manifest = read_json_file(manifest_path)
        if str(manifest.get("paper_id") or "") != state.paper_id:
            raise ValueError("final report paper mismatch")
        return out_dir, manifest

    def _report_artifact_path(self, state: ReviewJobState, relative_path: str) -> Path:
        if not relative_path:
            raise FileNotFoundError("report artifact is missing")
        out_dir = peerassist_stage_dir(self._run_dir(state))
        reports_root = (out_dir / "reports").resolve(strict=True)
        path = (out_dir / relative_path).resolve(strict=True)
        path.relative_to(reports_root)
        if not path.is_file():
            raise FileNotFoundError("report artifact is missing")
        return path

    def apply_decision(
        self, job_id: UUID | str, payload: dict[str, Any]
    ) -> tuple[ReviewJobState, dict[str, Any], dict[str, Any]]:
        state = self.repository.get(job_id)
        if state.status is not ReviewJobStatus.AWAITING_HUMAN_CONFIRMATION:
            raise ValueError("job_not_awaiting_human_confirmation")
        result = apply_confirmation_decision(
            run_dir=self._run_dir(state),
            paper_id=state.paper_id,
            concern_id=str(payload.get("concern_id") or ""),
            action=str(payload.get("action") or ""),
            reviewer_id=str(payload.get("reviewer_id") or "reviewer"),
            timestamp=str(payload.get("timestamp") or ""),
            previous_text=str(payload.get("previous_text") or ""),
            new_text=str(payload.get("new_text") or ""),
            reason=str(payload.get("reason") or ""),
            expected_revision=int(payload.get("confirmation_revision") or 0),
            expected_finding_lineage_id=str(payload.get("finding_lineage_id") or ""),
            expected_finding_id=str(payload.get("finding_id") or ""),
            expected_finding_revision=int(payload.get("finding_revision") or 0),
        )
        if result.get("status") != "ok":
            current, workspace = self.workspace(job_id)
            return current, result, workspace
        confirmation_revision = int(result.get("confirmation_revision") or 0)
        for _ in range(3):
            current = self.repository.get(job_id)
            try:
                state = self.repository.update(
                    job_id,
                    expected_revision=current.revision,
                    confirmation_revision=confirmation_revision,
                )
                break
            except RepositoryConflictError:
                continue
            except Exception as exc:
                raise DecisionCommitInterruptedError(
                    "decision_commit_interrupted"
                ) from exc
        else:
            raise DecisionCommitInterruptedError("decision_commit_interrupted")
        try:
            self._append_confirmation_event(
                state,
                concern_id=str(payload.get("concern_id") or ""),
                action=str(payload.get("action") or ""),
                confirmation_revision=confirmation_revision,
            )
        except Exception as exc:
            raise DecisionCommitInterruptedError("decision_commit_interrupted") from exc
        state, workspace = self.workspace(job_id)
        return state, result, workspace

    def _append_confirmation_event(
        self,
        state: ReviewJobState,
        *,
        concern_id: str,
        action: str,
        confirmation_revision: int,
    ) -> None:
        self.repository.append_event(
            state.id,
            "confirmation_decision_applied",
            stage=state.stage,
            status=state.status,
            attempt_id=state.attempt_id,
            payload={
                "concern_id": concern_id,
                "action": action,
                "confirmation_revision": confirmation_revision,
            },
        )

    def _reconcile_confirmation_commit(self, state: ReviewJobState) -> ReviewJobState:
        with self._decision_reconciliation_lock:
            run_dir = self._run_dir(state)
            confirmations = read_json_file(
                peerassist_stage_dir(run_dir) / "human_confirmations.json"
            )
            file_revision = max(0, int(confirmations.get("revision") or 0))
            if file_revision > state.confirmation_revision:
                for _ in range(3):
                    current = self.repository.get(state.id)
                    if current.confirmation_revision >= file_revision:
                        state = current
                        break
                    try:
                        state = self.repository.update(
                            state.id,
                            expected_revision=current.revision,
                            confirmation_revision=file_revision,
                        )
                        break
                    except RepositoryConflictError:
                        continue
                else:
                    raise RepositoryConflictError(
                        "unable to reconcile confirmation revision"
                    )

            events = self.repository.replay_events(state.id)
            recorded = {
                int(event.payload.get("confirmation_revision") or 0)
                for event in events
                if event.event_type == "confirmation_decision_applied"
            }
            actions = (
                confirmations.get("actions")
                if isinstance(confirmations.get("actions"), list)
                else []
            )
            for revision, row in enumerate(actions[:file_revision], start=1):
                if revision in recorded or not isinstance(row, dict):
                    continue
                self._append_confirmation_event(
                    state,
                    concern_id=str(row.get("concern_id") or ""),
                    action=str(row.get("action") or ""),
                    confirmation_revision=revision,
                )
            return self.repository.get(state.id)

    def _run_dir(self, state: ReviewJobState) -> Path:
        run_dir = (self.repository.data_dir / state.run_dir).resolve(strict=True)
        expected = (self.repository.jobs_dir / str(state.id) / "run").resolve(strict=True)
        if run_dir != expected:
            raise ValueError("job run directory mismatch")
        return run_dir

    def _stage_result(self, state: ReviewJobState, stage: ReviewStage) -> dict[str, Any]:
        manifest = self.repository.current_stage_manifest(state.id, stage)
        if manifest is None:
            return {}
        path = (
            self.repository.jobs_dir
            / str(state.id)
            / manifest.output_dir
            / manifest.artifacts.get("result", "result.json")
        )
        payload = read_json_file(path)
        return payload if isinstance(payload, dict) else {}


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
        request_url = urlsplit(self.path)
        path = request_url.path
        query = parse_qs(request_url.query)
        if path == "/api/health":
            self._send_json({"status": "ok", "service": "peerassist-review-jobs"})
            return
        if path == "/api/jobs":
            self._send_json(
                {
                    "jobs": [
                        self.service.job_view(state)
                        for state in self.service.repository.list()
                    ]
                }
            )
            return
        paper_match = _PAPER_SOURCE_RE.match(path)
        if paper_match is not None:
            self._send_paper_source(paper_match.group("paper"))
            return
        workspace_match = _JOB_WORKSPACE_RE.match(path)
        if workspace_match is not None:
            try:
                state, workspace = self.service.workspace(workspace_match.group("job"))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json({"job": state.model_dump(mode="json"), "state": workspace})
            return
        artifacts_match = _JOB_ARTIFACTS_RE.match(path)
        if artifacts_match is not None:
            try:
                payload = self.service.artifact_index(artifacts_match.group("job"))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=404)
                return
            self._send_json({"artifacts": payload})
            return
        artifact_match = _JOB_ARTIFACT_RE.match(path)
        if artifact_match is not None:
            self._send_job_artifact(
                artifact_match.group("job"),
                artifact_match.group("artifact"),
                inline=query.get("disposition") == ["inline"],
            )
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
        request_url = urlsplit(self.path)
        path = request_url.path
        query = parse_qs(request_url.query)
        artifact_match = _JOB_ARTIFACT_RE.match(path)
        if artifact_match is not None:
            self._send_job_artifact(
                artifact_match.group("job"),
                artifact_match.group("artifact"),
                head_only=True,
                inline=query.get("disposition") == ["inline"],
            )
            return
        match = _PAPER_SOURCE_RE.match(path)
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
        decision = _JOB_DECISION_RE.match(self.path)
        if decision is not None:
            try:
                state, result, workspace = self.service.apply_decision(
                    decision.group("job"), payload
                )
            except DecisionCommitInterruptedError as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            status = 200 if result.get("status") == "ok" else 409
            self._send_json(
                {
                    "job": state.model_dump(mode="json"),
                    "result": result,
                    "state": workspace,
                },
                status=status,
            )
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
            self.service.scheduler.submit(state.id)
        elif action == "/retry":
            state = self.service.runner.retry(job_id)
            self.service.scheduler.submit(state.id)
        elif action == "/finalize":
            try:
                state = self.service.runner.finalize(
                    job_id,
                    expected_confirmation_revision=int(
                        payload.get("confirmation_revision") or 0
                    ),
                    override_reason=str(payload.get("override_reason") or ""),
                )
            except ValueError as exc:
                if str(exc) != "confirmation_revision_conflict":
                    raise
                self._send_json({"error": str(exc)}, status=409)
                return
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
        return self.service.job_view(self.service.repository.get(job_id))

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

    def _send_job_artifact(
        self,
        job_id: str,
        artifact_name: str,
        *,
        head_only: bool = False,
        inline: bool = False,
    ) -> None:
        try:
            path, media_type = self.service.artifact_file(job_id, artifact_name)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=404, head_only=head_only)
            return
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        disposition = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", f'{disposition}; filename="{path.name}"')
        self.send_header("Cache-Control", "private, max-age=3600, immutable")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        if not head_only:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
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
