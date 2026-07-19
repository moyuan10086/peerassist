"""Claim durable review work and publish user-visible summary/report artifacts."""

from __future__ import annotations

import io
import json
import os
import re
import signal
import socket
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

import requests
from pypdf import PdfReader

from peerassist.model_review import resolve_model_review_config, run_model_review_text
from peerassist.platform.adapters.workspace import StageArtifactPublisher, WorkspaceMaterializer
from peerassist.platform.errors import DependencyUnavailable, NotFound
from peerassist.platform.models import (
    Actor,
    ActorKind,
    Artifact,
    OutboxEvent,
    ReviewEvent,
    ReviewJob,
    StageInputManifest,
    StageOutputSpec,
    TenantScope,
)
from peerassist.platform.ports import ObjectStore, UnitOfWorkFactory
from peerassist.platform.services.artifacts import ArtifactService


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ReviewWorker:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        object_store: ObjectStore,
        scratch_root: Path,
        *,
        document_generator: Callable[[bytes], tuple[str, str]] | None = None,
        clock=_utc_now,
    ) -> None:
        self._uow_factory = uow_factory
        self._object_store = object_store
        self._scratch_root = scratch_root
        self._materializer = WorkspaceMaterializer(object_store, scratch_root)
        self._publisher = StageArtifactPublisher(object_store)
        self._document_generator = document_generator or generate_review_documents
        self._clock = clock
        self.artifact_service = ArtifactService(uow_factory, object_store)

    def run_once(self, scope: TenantScope, *, worker_id: str) -> ReviewJob | None:
        service_actor = Actor(uuid5(NAMESPACE_URL, "peerassist:worker"), ActorKind.SERVICE)
        with self._uow_factory(service_actor) as uow:
            work = uow.work_items.claim(scope, worker_id, 300)
            if work is None:
                return None
            job = uow.review_jobs.get(scope, work.job_id)
            if job is None:
                raise NotFound()
            version = uow.papers.get_version(scope, job.paper_version_id)
            if version is None:
                raise NotFound()
            uow.commit()
        if job.status == "cancelled":
            with self._uow_factory(service_actor) as uow:
                uow.work_items.complete(scope, work.id, worker_id)
                uow.commit()
            return job
        descriptor = self._object_store.metadata(scope, version.source_object_id)
        if (
            descriptor is None
            or descriptor.size_bytes != version.size_bytes
            or descriptor.sha256 != version.sha256
        ):
            self._fail(scope, service_actor, work, worker_id, job, "source_unavailable")
            raise DependencyUnavailable()
        workspace = (
            self._scratch_root
            / str(job.id)
            / f"attempt-{job.attempt}"
            / work.stage
        )
        try:
            materialized = self._materializer.materialize(
                scope,
                job,
                work.stage,
                StageInputManifest(work.input_revision, (descriptor,)),
                workspace,
            )
            pdf_bytes = (materialized / "inputs" / "object-000.bin").read_bytes()
            summary, report = self._document_generator(pdf_bytes)
            outputs = materialized / "outputs"
            (outputs / "paper_summary.md").write_text(summary.rstrip() + "\n", encoding="utf-8")
            (outputs / "review.md").write_text(report.rstrip() + "\n", encoding="utf-8")
            logical_names = ("paper_summary.md", "review.md")
            descriptors = self._publisher.publish(
                scope,
                job,
                "report",
                StageOutputSpec(logical_names, 4 * 1024 * 1024),
                materialized,
            )
            with self._uow_factory(Actor(job.created_by, ActorKind.USER)) as uow:
                current = uow.review_jobs.get(scope, job.id)
                if current is None:
                    raise NotFound()
                existing = {
                    artifact.logical_name: artifact
                    for artifact in uow.artifacts.list_for_job(scope, job.id)
                }
                for logical_name, published in zip(logical_names, descriptors, strict=True):
                    if logical_name in existing:
                        if existing[logical_name].object != published:
                            raise ValueError("artifact replay does not match immutable object")
                        continue
                    uow.artifacts.add(
                        scope,
                        Artifact(
                            uuid5(job.id, f"artifact:{logical_name}"),
                            scope.organization_id,
                            job.project_id,
                            job.id,
                            logical_name,
                            published,
                            "available",
                            job.updated_at,
                        ),
                    )
                completed = replace(
                    current,
                    stage="finalize",
                    status="blocked",
                    version=current.version + 1,
                    updated_at=self._clock(),
                )
                uow.review_jobs.save(scope, completed, current.version)
                sequence = len(uow.review_jobs.list_events(scope, job.id)) + 1
                event_type = "review_job.artifacts_published"
                uow.review_jobs.append_event(
                    scope,
                    ReviewEvent(
                        uuid5(job.id, f"event:{sequence}:{event_type}"),
                        scope.organization_id,
                        job.project_id,
                        job.id,
                        sequence,
                        event_type,
                        1,
                        {"artifacts": list(logical_names), "status": completed.status},
                        self._clock(),
                    ),
                )
                uow.outbox.append(
                    scope,
                    OutboxEvent(
                        uuid5(job.id, f"outbox:{sequence}:{event_type}"),
                        scope.organization_id,
                        "review_job",
                        job.id,
                        sequence,
                        event_type,
                        1,
                        {"job_id": str(job.id)},
                        self._clock(),
                        project_id=job.project_id,
                    ),
                )
                uow.work_items.complete(scope, work.id, worker_id)
                uow.commit()
                return completed
        except Exception:
            self._fail(scope, service_actor, work, worker_id, job, "worker_stage_failed")
            raise
        finally:
            self._materializer.cleanup(workspace)

    def _fail(
        self,
        scope: TenantScope,
        actor: Actor,
        work,
        worker_id: str,
        job: ReviewJob,
        error_code: str,
    ) -> None:
        with self._uow_factory(actor) as uow:
            current_work = uow.work_items.get(scope, work.id)
            if current_work is not None and current_work.lease_owner == worker_id:
                uow.work_items.fail(scope, current_work, worker_id)
            current = uow.review_jobs.get(scope, job.id)
            if current is not None and current.status not in {"cancelled", "completed", "failed"}:
                uow.review_jobs.save(
                    scope,
                    replace(
                        current,
                        status="failed",
                        version=current.version + 1,
                        updated_at=self._clock(),
                        safe_error_code=error_code,
                    ),
                    current.version,
                )
            uow.commit()


def generate_review_documents(pdf_bytes: bytes) -> tuple[str, str]:
    title, text = _extract_pdf(pdf_bytes)
    config = resolve_model_review_config()
    if config is not None and text:
        try:
            return _generate_with_model(title, text, config)
        except Exception:
            pass
    return _fallback_documents(title, text)


def _extract_pdf(pdf_bytes: bytes) -> tuple[str, str]:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        title = str((reader.metadata or {}).get("/Title") or "").strip()
        pages = [(page.extract_text() or "").strip() for page in reader.pages[:20]]
    except Exception:
        return "", ""
    text = re.sub(r"\s+", " ", " ".join(pages)).strip()
    return title, text[:24000]


def _generate_with_model(title: str, text: str, config) -> tuple[str, str]:
    prompt = json.dumps(
        {
            "title": title,
            "paper_text": text[:18000],
            "output": {
                "summary_markdown": "中文，包含一句话总结、研究问题、方法、主要发现、局限",
                "review_markdown": "中文，包含论文概要、优点、主要问题、次要问题、建议",
            },
        },
        ensure_ascii=False,
    )
    system = (
        "你是证据忠实的论文审阅助手。只根据提供的论文文本总结，不补造事实。"
        "返回严格 JSON，字段为 summary_markdown 和 review_markdown。"
    )
    if config.provider == "openai-codex":
        result = run_model_review_text(system=system, prompt=prompt, config=config)
        raw = result if isinstance(result, str) else result[0]
    else:
        response = requests.post(
            config.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {config.api_key}"},
            json={
                "model": config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "max_tokens": max(1200, config.max_tokens),
            },
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
        raw = str(response.json()["choices"][0]["message"]["content"])
    payload = _json_object(raw)
    summary = str(payload.get("summary_markdown") or "").strip()
    report = str(payload.get("review_markdown") or "").strip()
    if not summary or not report:
        raise ValueError("model response omitted review documents")
    return summary, report


def _json_object(raw: str) -> dict[str, object]:
    candidate = raw.strip()
    if "```" in candidate:
        candidate = next(
            (
                block.strip().removeprefix("json").strip()
                for block in candidate.split("```")
                if block.strip().removeprefix("json").strip().startswith("{")
            ),
            candidate,
        )
    start, end = candidate.find("{"), candidate.rfind("}")
    payload = json.loads(candidate[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("model response is not an object")
    return payload


def _fallback_documents(title: str, text: str) -> tuple[str, str]:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s+", text)
        if len(sentence.strip()) >= 24
    ]
    objective = sentences[0] if sentences else "当前 PDF 未提取到可读正文，需要人工查看原文。"
    method = next(
        (row for row in sentences if re.search(r"\b(method|approach|framework|model)\b", row, re.I)),
        sentences[1] if len(sentences) > 1 else objective,
    )
    result = next(
        (row for row in sentences if re.search(r"\b(result|outperform|improv|experiment)\w*\b", row, re.I)),
        sentences[2] if len(sentences) > 2 else objective,
    )
    display_title = title or "当前论文"
    summary = (
        f"# 这篇论文讲了什么\n\n## {display_title}\n\n"
        f"## 一句话总结\n\n本文围绕以下核心内容展开：{objective}\n\n"
        f"## 研究方法\n\n{method}\n\n"
        f"## 主要发现\n\n{result}\n\n"
        "## 阅读提示\n\n本摘要由本地文本抽取生成，尚未调用已配置模型，关键结论请对照原文核验。"
    )
    report = (
        f"# 审阅报告\n\n## 论文概要\n\n{objective}\n\n"
        f"## 方法概览\n\n{method}\n\n"
        f"## 结果概览\n\n{result}\n\n"
        "## 待人工确认\n\n- 核对主要结论是否有直接实验或理论证据。\n"
        "- 核对方法描述是否足以复现。\n"
        "- 核对局限性与适用边界是否完整披露。"
    )
    return summary, report


def main() -> int:
    try:
        scope = _worker_scope_from_environment(os.environ)
    except (OSError, ValueError):
        return 2
    from common.config import PlatformSettings
    from services.api.composition import build_dependencies

    settings = PlatformSettings()
    dependencies = build_dependencies(settings)
    worker = ReviewWorker(
        dependencies.uow_factory,
        dependencies.object_store,
        settings.scratch_root,
    )
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    while not stopping:
        processed = worker.run_once(scope, worker_id=worker_id)
        if processed is None:
            time.sleep(1.0)
    return 0


def _worker_scope_from_environment(environ: Mapping[str, str]) -> TenantScope:
    organization_id = environ.get("PEERASSIST_WORKER_ORGANIZATION_ID", "").strip()
    project_id = environ.get("PEERASSIST_WORKER_PROJECT_ID", "").strip()
    if organization_id and project_id:
        return TenantScope(UUID(organization_id), UUID(project_id))
    scope_file = environ.get("PEERASSIST_WORKER_SCOPE_FILE", "").strip()
    if not scope_file:
        raise ValueError("worker tenant scope is unavailable")
    path = Path(scope_file)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError("worker tenant scope file is invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("worker tenant scope file is invalid")
    return TenantScope(UUID(str(payload["organization_id"])), UUID(str(payload["project_id"])))


if __name__ == "__main__":
    raise SystemExit(main())
