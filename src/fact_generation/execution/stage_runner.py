from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.config import get_settings
from common.pipeline_context import (
    ensure_full_pipeline_context,
    execution_stage_dir,
    load_bridge_state,
    read_json_file,
    write_json_file,
)
from llm.client import resolve_llm_config
from schemas.execution import ExecutionExitStatus, ExecutionPayload, ExecutionStageStatus
from schemas.stage import StageResult
from util.paper_input import infer_paper_key, is_url


def _url_looks_like_pdf_document(url: str) -> bool:
    raw = str(url or "").strip()
    if not is_url(raw):
        return False
    from urllib.parse import unquote, urlparse

    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    path = unquote(parsed.path or "").lower()
    if host.endswith("arxiv.org") and (path.startswith("/abs/") or path.startswith("/pdf/")):
        return True
    return path.endswith(".pdf") or "/pdf/" in path


def _url_looks_like_source_locator(url: str) -> bool:
    raw = str(url or "").strip()
    if not is_url(raw) or _url_looks_like_pdf_document(raw):
        return False
    from urllib.parse import urlparse

    host = urlparse(raw).netloc.lower()
    return any(
        token in host
        for token in (
            "openreview.net",
            "github.com",
            "gitlab.com",
            "bitbucket.org",
            "anonymous.4open.science",
        )
    )


def _load_execution_artifacts(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    run_info = state.get("run") if isinstance(state.get("run"), dict) else {}
    run_dir = Path(str(run_info.get("dir") or "")).resolve() if run_info.get("dir") else Path()
    summary = read_json_file(run_dir / "summary.json") if run_dir else {}
    alignment = read_json_file(run_dir / "artifacts" / "alignment" / "alignment.json") if run_dir else {}
    return summary, alignment, str(run_dir) if run_dir else ""


def _archive_prior_current_dir(*, stage_root: Path, current_dir: Path) -> Path:
    """Set up an empty ``current/`` workspace, preserving the prior attempt
    by renaming it to ``current.<timestamp>`` instead of deleting outright.
    A separate ``history/`` tree (passed as ``run_root`` to the orchestrator)
    holds full per-attempt outputs; this archive only protects the most
    recent in-place workspace from being silently wiped.

    On Windows, an interrupted subprocess can leave transient handles under
    ``current/.git``. If the archival rename is denied, do not crash the whole
    stage: run in an isolated active directory and leave a warning artifact.
    """
    resolved_stage = stage_root.resolve()
    resolved_current = current_dir.resolve()
    if resolved_current.parent != resolved_stage or resolved_current.name != "current":
        raise RuntimeError(f"refusing to reset unexpected execution current dir: {resolved_current}")
    target_current = resolved_current
    if resolved_current.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archived = resolved_current.with_name(f"current.{timestamp}")
        try:
            resolved_current.rename(archived)
        except OSError as exc:
            target_current = resolved_current.with_name(f"current.active.{timestamp}")
            write_json_file(
                resolved_stage / f"current_archive_warning.{timestamp}.json",
                {
                    "current_dir": str(resolved_current),
                    "active_dir": str(target_current),
                    "archive_dir": str(archived),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
    target_current.mkdir(parents=True, exist_ok=True)
    return target_current


async def _run_orchestrator_async(
    *,
    run_root: Path,
    paper_pdf: Path | str | None,
    paper_key: str,
    max_attempts: int,
    no_pdf_extract: bool,
    paper_root: str = "",
    paper_repo_url: str = "",
    paper_extracted_dir: str = "",
    execution_run_dir: Path | None = None,
    enable_refcheck: bool = False,
    no_llm: bool = False,
    auto_tasks: bool = False,
    auto_tasks_mode: str = "smoke",
    auto_tasks_force: bool = False,
    paper_budget_sec: int = 0,
    docker_enabled: bool | None = None,
    docker_build_timeout_sec: int = 0,
    llm_provider: str = "",
    llm_model: str = "",
    llm_base_url: str = "",
) -> dict[str, Any]:
    from fact_generation.execution.graph import ExecutionOrchestrator

    orchestrator = ExecutionOrchestrator(
        run_root=str(run_root),
        max_attempts=max_attempts,
        no_llm=no_llm,
        enable_refcheck=enable_refcheck,
        paper_extracted_dir=str(paper_extracted_dir or ""),
        run_dir=str(execution_run_dir or ""),
        auto_tasks=auto_tasks,
        auto_tasks_mode=auto_tasks_mode,
        auto_tasks_force=auto_tasks_force,
        paper_budget_sec=paper_budget_sec,
        docker_enabled=docker_enabled,
        docker_build_timeout_sec=docker_build_timeout_sec,
        paper_repo_url=paper_repo_url,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_base_url=llm_base_url,
    )
    return await orchestrator.run(
        paper_root=str(paper_root or ""),
        paper_pdf=str(paper_pdf or ""),
        paper_key=paper_key,
        tasks_path="",
        baseline_path="",
        local_source_path="",
        no_pdf_extract=no_pdf_extract,
        paper_repo_url=paper_repo_url,
    )


def run_execution_stage(
    *,
    run_dir: Path,
    paper_pdf: Path | str | None = None,
    paper_key: str | None = None,
    paper_root: str = "",
    paper_repo_url: str = "",
    paper_extracted_dir: str = "",
    max_attempts: int = 5,
    no_pdf_extract: bool = False,
    enable_refcheck: bool | None = None,
    no_llm: bool = False,
    auto_tasks: bool = False,
    auto_tasks_mode: str = "smoke",
    auto_tasks_force: bool = False,
    paper_budget_sec: int = 0,
    docker_enabled: bool | None = None,
    docker_build_timeout_sec: int = 0,
) -> StageResult:
    ensure_full_pipeline_context(run_dir=run_dir, allow_standalone=True, stage="execution")
    bridge = load_bridge_state(run_dir)
    raw_pdf = str(paper_pdf or "").strip()
    paper_pdf_for_orchestrator = ""
    resolved_pdf: Path | None = None
    if raw_pdf:
        if is_url(raw_pdf):
            if _url_looks_like_source_locator(raw_pdf):
                if not str(paper_repo_url or "").strip():
                    paper_repo_url = raw_pdf
                raw_pdf = ""
            else:
                paper_pdf_for_orchestrator = raw_pdf
        if raw_pdf and is_url(raw_pdf):
            paper_pdf_for_orchestrator = raw_pdf
        elif raw_pdf:
            resolved_pdf = Path(raw_pdf).expanduser().resolve()
            paper_pdf_for_orchestrator = str(resolved_pdf)
    elif bridge:
        resolved_pdf = bridge.paper_pdf
        paper_pdf_for_orchestrator = str(resolved_pdf or "")
    resolved_key = (paper_key or "").strip() or (bridge.paper_key if bridge else "")
    source_hint = str(paper_root or paper_repo_url or "").strip()

    local_pdf_missing = resolved_pdf is not None and not resolved_pdf.exists()
    if (not paper_pdf_for_orchestrator or local_pdf_missing) and not source_hint:
        raise FileNotFoundError(
            "paper_pdf is required for execution stage when bridge state is missing or invalid."
        )
    if not resolved_key:
        if resolved_pdf is not None:
            resolved_key = resolved_pdf.stem.strip()
        elif paper_pdf_for_orchestrator:
            resolved_key = infer_paper_key(paper_pdf_for_orchestrator)
        elif paper_root:
            resolved_key = Path(paper_root).resolve().name
        elif paper_repo_url:
            resolved_key = infer_paper_key(paper_repo_url)
        resolved_key = resolved_key or "paper"

    stage_root = execution_stage_dir(run_dir)
    stage_root.mkdir(parents=True, exist_ok=True)
    # ``current/`` = the in-place workspace the orchestrator scribbles into.
    # ``history/`` = the orchestrator's per-attempt outputs root (one
    # timestamped subdir per attempt). The names are intentionally distinct.
    execution_run_dir = stage_root / "current"
    stage_run_root = stage_root / "history"
    execution_run_dir = _archive_prior_current_dir(stage_root=stage_root, current_dir=execution_run_dir)
    settings = get_settings()
    resolved_refcheck = bool(
        settings.execution_enable_refcheck if enable_refcheck is None else enable_refcheck
    )
    llm_provider = ""
    llm_model = ""
    llm_base_url = ""
    if not no_llm:
        try:
            llm_cfg = resolve_llm_config()
            llm_provider = llm_cfg.provider
            llm_model = llm_cfg.model
            llm_base_url = llm_cfg.base_url or ""
        except Exception:
            pass

    run_result = asyncio.run(
        _run_orchestrator_async(
            run_root=stage_run_root,
            paper_pdf=paper_pdf_for_orchestrator,
            paper_key=resolved_key,
            paper_root=paper_root,
            paper_repo_url=paper_repo_url,
            paper_extracted_dir=str(paper_extracted_dir or ""),
            execution_run_dir=execution_run_dir,
            max_attempts=max_attempts,
            no_pdf_extract=no_pdf_extract,
            enable_refcheck=resolved_refcheck,
            no_llm=no_llm,
            auto_tasks=auto_tasks,
            auto_tasks_mode=auto_tasks_mode,
            auto_tasks_force=auto_tasks_force,
            paper_budget_sec=paper_budget_sec,
            docker_enabled=docker_enabled,
            docker_build_timeout_sec=docker_build_timeout_sec,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
        )
    )

    state = run_result.get("state") if isinstance(run_result.get("state"), dict) else {}
    summary, alignment, actual_run_dir = _load_execution_artifacts(state)
    raw_exit = str(run_result.get("exit_status") or "failed")
    exit_status: ExecutionExitStatus = (
        raw_exit if raw_exit in ("success", "inconclusive", "failed", "skipped", "partial") else "failed"  # type: ignore[assignment]
    )

    stage_status: ExecutionStageStatus = "failed"
    if exit_status == "success":
        stage_status = "ok"
    elif exit_status in {"inconclusive", "partial"}:
        stage_status = "inconclusive"
    elif exit_status == "skipped":
        stage_status = "skipped"

    error = ""
    if stage_status == "failed":
        detail = str(run_result.get("error") or run_result.get("message") or "").strip()
        error = (
            f"execution orchestrator exit_status={exit_status!r}: {detail}"
            if detail
            else f"execution orchestrator exit_status={exit_status!r}"
        )

    payload = ExecutionPayload(
        paper_key=resolved_key,
        paper_pdf=str(paper_pdf_for_orchestrator or resolved_pdf or ""),
        status=stage_status,
        success=bool(run_result.get("success")),
        exit_status=exit_status,
        run_dir=actual_run_dir,
        summary=summary,
        alignment=alignment,
    )

    output_path = stage_root / "execution.json"
    write_json_file(output_path, payload.model_dump())

    return StageResult(
        status=stage_status,
        outputs={"main": str(output_path)},
        extra={"run_dir": actual_run_dir},
        error=error,
    )


if __name__ == "__main__":
    raise SystemExit("Internal stage module. Use scripts/execute_review_pipeline.py.")
