"""Command line entrypoint for PeerAssist-Eval-v1 aggregate reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common.pipeline_context import write_json_file
from peerassist.eval_harness import evaluate_peerassist_records
from peerassist.eval_record_builder import build_eval_record_from_artifacts


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
    subparsers = parser.add_subparsers(dest="command")

    aggregate = subparsers.add_parser("aggregate", help="Aggregate eval records against target gates.")
    aggregate.add_argument("--manifest", required=True, help="Path to PeerAssist-Eval-v1 manifest JSON.")
    aggregate.add_argument("--records", required=True, help="Path to eval records JSON or JSONL.")
    aggregate.add_argument("--out", required=True, help="Path to write eval report JSON.")

    record = subparsers.add_parser("record", help="Build one eval record from PeerAssist artifacts.")
    record.add_argument("--sample-id", required=True)
    record.add_argument("--run-dir", required=True)
    record.add_argument("--gold", required=True)
    record.add_argument("--out", required=True)

    parser.add_argument("--manifest", help=argparse.SUPPRESS)
    parser.add_argument("--records", help=argparse.SUPPRESS)
    parser.add_argument("--out", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "record":
        record = build_eval_record_from_artifacts(
            sample_id=args.sample_id,
            run_dir=Path(args.run_dir),
            gold_path=Path(args.gold),
        )
        write_json_file(Path(args.out), record)
        print(
            json.dumps(
                {
                    "schema_version": "peerassist.eval_record_cli_result.v1",
                    "record_path": str(Path(args.out)),
                    "sample_id": record["sample_id"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    if not args.command and args.manifest and args.records and args.out:
        # Backward-compatible aggregate mode for the original CLI shape.
        pass
    elif args.command not in {None, "aggregate"}:
        parser.error("unknown command")
    elif not args.manifest or not args.records or not args.out:
        parser.error("--manifest, --records, and --out are required for aggregate mode")

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
