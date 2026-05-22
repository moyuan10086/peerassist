from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from common.env import load_env_file
from fact_generation.execution.stage_runner import run_execution_stage
from util.paper_input import infer_paper_key


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser("factreview_stage_execution")
    p.add_argument("--run-dir", type=str, required=True, help="Run directory to write stage outputs")
    p.add_argument("--paper-pdf", type=str, default="", help="Optional explicit paper PDF path or URL")
    p.add_argument("--paper-root", type=str, default="", help="Optional local repository/source directory")
    p.add_argument("--paper-repo-url", type=str, default="", help="Optional repository URL to clone")
    p.add_argument("--paper-key", type=str, default="")
    p.add_argument(
        "--paper-extracted-dir", type=str, default="", help="Optional run-local MinerU extract snapshot"
    )
    p.add_argument("--max-attempts", type=int, default=5)
    p.add_argument("--no-pdf-extract", action="store_true")
    p.add_argument("--enable-refcheck", action="store_true")
    p.add_argument("--no-llm", action="store_true", help="Disable LLM-assisted task inference/fixes")
    p.add_argument("--auto-tasks", action="store_true", help="Generate tasks.yaml from the repo/paper")
    p.add_argument("--auto-tasks-mode", choices=("smoke", "full"), default="smoke")
    p.add_argument("--auto-tasks-force", action="store_true")
    p.add_argument(
        "--paper-budget-sec",
        type=int,
        default=0,
        help=(
            "Optional soft per-paper execution budget. Disabled unless "
            "EXECUTION_ENABLE_PAPER_BUDGET=1 is set; 0 disables the budget."
        ),
    )
    p.add_argument(
        "--docker-build-timeout-sec",
        type=int,
        default=0,
        help="Per-paper Docker image build timeout; 0 disables the timeout",
    )
    docker_group = p.add_mutually_exclusive_group()
    docker_group.add_argument("--docker", dest="docker_enabled", action="store_true", default=None)
    docker_group.add_argument("--no-docker", dest="docker_enabled", action="store_false")
    p.add_argument("--docker-gpus", type=str, default="", help="Value for docker run --gpus, e.g. all")
    p.add_argument("--docker-shm-size", type=str, default="", help="Value for docker run --shm-size")
    p.add_argument("--docker-ipc", type=str, default="", help="Value for docker run --ipc")
    p.add_argument("--pip-index-url", type=str, default="", help="Docker build pip index URL")
    p.add_argument("--pip-extra-index-url", type=str, default="", help="Docker build pip extra index URL")
    p.add_argument("--pip-trusted-host", type=str, default="", help="Docker build pip trusted-host value")
    return p.parse_args()


def main() -> None:
    load_env_file(ROOT / ".env")
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    if str(args.docker_gpus or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_GPUS"] = str(args.docker_gpus).strip()
    if str(args.docker_shm_size or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_SHM_SIZE"] = str(args.docker_shm_size).strip()
    if str(args.docker_ipc or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_IPC"] = str(args.docker_ipc).strip()
    if str(args.pip_index_url or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_PIP_INDEX_URL"] = str(args.pip_index_url).strip()
    if str(args.pip_extra_index_url or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_PIP_EXTRA_INDEX_URL"] = str(args.pip_extra_index_url).strip()
    if str(args.pip_trusted_host or "").strip():
        import os

        os.environ["EXECUTION_DOCKER_PIP_TRUSTED_HOST"] = str(args.pip_trusted_host).strip()
    paper_key = str(args.paper_key or "").strip() or (
        infer_paper_key(args.paper_pdf) if str(args.paper_pdf or "").strip() else ""
    )
    result = run_execution_stage(
        run_dir=run_dir,
        paper_pdf=str(args.paper_pdf or "").strip() or None,
        paper_key=paper_key,
        paper_root=str(args.paper_root or "").strip(),
        paper_repo_url=str(args.paper_repo_url or "").strip(),
        paper_extracted_dir=str(args.paper_extracted_dir or "").strip(),
        max_attempts=int(args.max_attempts),
        no_pdf_extract=bool(args.no_pdf_extract),
        enable_refcheck=bool(args.enable_refcheck),
        no_llm=bool(args.no_llm),
        auto_tasks=bool(args.auto_tasks),
        auto_tasks_mode=str(args.auto_tasks_mode or "smoke"),
        auto_tasks_force=bool(args.auto_tasks_force),
        paper_budget_sec=int(args.paper_budget_sec or 0),
        docker_enabled=args.docker_enabled,
        docker_build_timeout_sec=int(args.docker_build_timeout_sec or 0),
    )
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
