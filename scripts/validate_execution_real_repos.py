from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.env import load_env_file
from fact_generation.execution.stage_runner import run_execution_stage
from util.run_layout import slugify_run_key

DEFAULT_REPOS: list[tuple[str, str]] = [
    ("bioprotocolbench", "https://github.com/YuyangSunshine/bioprotocolbench"),
    ("explaind", "https://github.com/mainlp/explaind"),
    ("compgcn", "https://github.com/malllabiisc/CompGCN"),
    ("fixmatch", "https://github.com/google-research/fixmatch"),
    ("graphormer", "https://github.com/microsoft/Graphormer"),
]


@dataclass
class RepoRunSummary:
    key: str
    repo_url: str
    status: str
    exit_status: str
    run_dir: str
    duration_sec: float
    error: str = ""
    output: str = ""


def _parse_repo_spec(raw: str) -> tuple[str, str]:
    token = str(raw or "").strip()
    if not token:
        raise ValueError("empty repo spec")
    if "=" in token:
        key, url = token.split("=", 1)
    else:
        url = token
        key = token.rstrip("/").split("/")[-1].replace(".git", "")
    key = slugify_run_key(key.strip() or "repo")
    url = url.strip()
    if not url:
        raise ValueError(f"missing repo URL in spec: {raw!r}")
    return key, url


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("factreview_validate_execution_real_repos")
    p.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Repository to test, as key=https://... or just https://...; repeatable",
    )
    p.add_argument("--run-root", type=str, default="runs/execution_real_repos")
    p.add_argument("--max-repos", type=int, default=5)
    p.add_argument("--max-attempts", type=int, default=1)
    p.add_argument("--auto-tasks-mode", choices=("smoke", "full"), default="smoke")
    p.add_argument("--paper-budget-sec", type=int, default=900)
    p.add_argument("--clone-timeout-sec", type=int, default=600)
    p.add_argument("--docker-build-timeout-sec", type=int, default=1800)
    p.add_argument("--no-llm", action="store_true")
    docker_group = p.add_mutually_exclusive_group()
    docker_group.add_argument("--docker", dest="docker_enabled", action="store_true", default=None)
    docker_group.add_argument("--no-docker", dest="docker_enabled", action="store_false")
    p.add_argument("--docker-gpus", type=str, default="")
    p.add_argument("--docker-shm-size", type=str, default="")
    p.add_argument("--docker-ipc", type=str, default="")
    return p.parse_args()


def _set_env_if_value(name: str, value: str) -> None:
    token = str(value or "").strip()
    if token:
        os.environ[name] = token


def main() -> None:
    args = parse_args()
    load_env_file(ROOT / ".env")
    _set_env_if_value("EXECUTION_DOCKER_GPUS", args.docker_gpus)
    _set_env_if_value("EXECUTION_DOCKER_SHM_SIZE", args.docker_shm_size)
    _set_env_if_value("EXECUTION_DOCKER_IPC", args.docker_ipc)
    os.environ["EXECUTION_GIT_CLONE_TIMEOUT_SEC"] = str(int(args.clone_timeout_sec or 600))

    repos = [_parse_repo_spec(raw) for raw in args.repo] if args.repo else list(DEFAULT_REPOS)
    max_repos = int(args.max_repos or 0)
    if max_repos > 0:
        repos = repos[:max_repos]

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = (ROOT / args.run_root).resolve() if not Path(args.run_root).is_absolute() else Path(args.run_root)
    run_root.mkdir(parents=True, exist_ok=True)

    summaries: list[RepoRunSummary] = []
    for idx, (key, repo_url) in enumerate(repos, 1):
        print(f"[{idx}/{len(repos)}] execution validation: {key} -> {repo_url}", flush=True)
        run_dir = run_root / f"{idx:02d}_{key}_{stamp}"
        t0 = time.monotonic()
        try:
            result = run_execution_stage(
                run_dir=run_dir,
                paper_key=key,
                paper_repo_url=repo_url,
                max_attempts=int(args.max_attempts or 1),
                no_pdf_extract=True,
                no_llm=bool(args.no_llm),
                auto_tasks=True,
                auto_tasks_mode=str(args.auto_tasks_mode or "smoke"),
                auto_tasks_force=True,
                paper_budget_sec=int(args.paper_budget_sec or 0),
                docker_enabled=args.docker_enabled,
                docker_build_timeout_sec=int(args.docker_build_timeout_sec or 0),
            )
            payload: dict[str, Any] = {}
            output = str(result.outputs.get("main") or "")
            if output and Path(output).exists():
                try:
                    payload = json.loads(Path(output).read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    payload = {}
            summaries.append(
                RepoRunSummary(
                    key=key,
                    repo_url=repo_url,
                    status=str(result.status),
                    exit_status=str(payload.get("exit_status") or ""),
                    run_dir=str(run_dir),
                    duration_sec=round(time.monotonic() - t0, 2),
                    error=str(result.error or ""),
                    output=output,
                )
            )
        except Exception as exc:
            summaries.append(
                RepoRunSummary(
                    key=key,
                    repo_url=repo_url,
                    status="failed",
                    exit_status="failed",
                    run_dir=str(run_dir),
                    duration_sec=round(time.monotonic() - t0, 2),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    out_path = run_root / f"summary_{stamp}.json"
    payload = {
        "created_at": stamp,
        "mode": str(args.auto_tasks_mode or "smoke"),
        "docker_enabled": args.docker_enabled,
        "paper_budget_sec": int(args.paper_budget_sec or 0),
        "docker_build_timeout_sec": int(args.docker_build_timeout_sec or 0),
        "repos": [asdict(item) for item in summaries],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    print(f"summary: {out_path}", flush=True)


if __name__ == "__main__":
    main()
