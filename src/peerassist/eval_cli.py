"""Command line entrypoint for PeerAssist-Eval-v1 aggregate reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common.pipeline_context import write_json_file
from peerassist.eval_harness import evaluate_peerassist_records


def load_eval_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows

    payload = json.loads(text)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        return [row for row in payload["records"] if isinstance(row, dict)]
    raise ValueError("eval records must be a JSON array, JSON object with records, or JSONL")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate PeerAssist-Eval-v1 aggregate metrics.")
    parser.add_argument("--manifest", required=True, help="Path to PeerAssist-Eval-v1 manifest JSON.")
    parser.add_argument("--records", required=True, help="Path to eval records JSON or JSONL.")
    parser.add_argument("--out", required=True, help="Path to write eval report JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest_path = Path(args.manifest)
    records_path = Path(args.records)
    out_path = Path(args.out)
    report = evaluate_peerassist_records(
        manifest_path=manifest_path,
        records=load_eval_records(records_path),
    )
    write_json_file(out_path, report)
    summary = {
        "schema_version": "peerassist.eval_cli_result.v1",
        "report_path": str(out_path),
        "all_targets_passed": bool(report["targets"]["all_targets_passed"]),
        "sample_count": report["sample_count"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["all_targets_passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
