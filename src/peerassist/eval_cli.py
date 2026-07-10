"""Command line entrypoint for PeerAssist-Eval-v1 aggregate reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common.pipeline_context import write_json_file
from peerassist.eval_harness import evaluate_peerassist_records, load_eval_manifest
from peerassist.eval_record_builder import build_eval_record_from_artifacts
from peerassist.reviewer_crossover import analyze_reviewer_crossover


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

    batch = subparsers.add_parser("batch-records", help="Build eval records for manifest samples.")
    batch.add_argument("--manifest", required=True)
    batch.add_argument("--out", required=True)

    crossover = subparsers.add_parser("crossover", help="Analyze reviewer crossover raw trials.")
    crossover.add_argument("--input", required=True, help="Path to reviewer crossover JSON or JSONL.")
    crossover.add_argument("--out", required=True, help="Path to write crossover report JSON.")
    crossover.add_argument(
        "--records-out",
        default="",
        help="Optional path to write eval-ready crossover records as JSONL.",
    )

    merge = subparsers.add_parser("merge-records", help="Merge eval record JSONL files by sample_id.")
    merge.add_argument("--base-records", required=True)
    merge.add_argument("--overlay-records", required=True)
    merge.add_argument("--out", required=True)

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

    if args.command == "batch-records":
        result = build_records_from_manifest(manifest_path=Path(args.manifest), out_path=Path(args.out))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not result["skipped_sample_ids"] else 1

    if args.command == "crossover":
        result = analyze_reviewer_crossover(Path(args.input))
        write_json_file(Path(args.out), result)
        if str(args.records_out or "").strip():
            _write_records_jsonl(Path(args.records_out), result["records"])
        print(
            json.dumps(
                {
                    "schema_version": "peerassist.crossover_cli_result.v1",
                    "report_path": str(Path(args.out)),
                    "records_out": str(args.records_out or ""),
                    "records_count": len(result["records"]),
                    "warnings": result["warnings"],
                    "recall_regression_warnings": result["recall_regression_warnings"],
                },
                ensure_ascii=False,
            )
        )
        return 0 if not result["warnings"] and not result["recall_regression_warnings"] else 1

    if args.command == "merge-records":
        records = merge_eval_records(
            base_records=load_eval_records(Path(args.base_records)),
            overlay_records=load_eval_records(Path(args.overlay_records)),
        )
        _write_records_jsonl(Path(args.out), records)
        print(
            json.dumps(
                {
                    "schema_version": "peerassist.eval_merge_records_result.v1",
                    "records_path": str(Path(args.out)),
                    "records_count": len(records),
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
        "failed_target_names": report["failed_target_names"],
        "failed_gate_names": report["failed_gate_names"],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["all_targets_passed"] else 1


def build_records_from_manifest(*, manifest_path: Path, out_path: Path) -> dict[str, Any]:
    manifest = load_eval_manifest(manifest_path)
    records: list[dict[str, Any]] = []
    skipped: list[str] = []
    for sample in manifest["samples"]:
        if not isinstance(sample, dict):
            continue
        sample_id = str(sample.get("sample_id") or "")
        run_dir = str(sample.get("run_dir") or "")
        gold_path = str(sample.get("gold_path") or "")
        if not sample_id or not run_dir or not gold_path:
            if sample_id:
                skipped.append(sample_id)
            continue
        records.append(
            build_eval_record_from_artifacts(
                sample_id=sample_id,
                run_dir=Path(run_dir),
                gold_path=Path(gold_path),
            )
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return {
        "schema_version": "peerassist.eval_batch_records_result.v1",
        "records_path": str(out_path),
        "records_count": len(records),
        "skipped_sample_ids": skipped,
    }


def _write_records_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def merge_eval_records(
    *, base_records: list[dict[str, Any]], overlay_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    overlays = {
        str(row.get("sample_id")): row
        for row in overlay_records
        if str(row.get("sample_id") or "").strip()
    }
    merged: list[dict[str, Any]] = []
    for row in base_records:
        sample_id = str(row.get("sample_id") or "").strip()
        combined = dict(row)
        if sample_id in overlays:
            for key, value in overlays[sample_id].items():
                if key == "sample_id":
                    continue
                combined[key] = value
        merged.append(combined)
    return merged


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
