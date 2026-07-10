"""CLI for applying PeerAssist human confirmation decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from peerassist.confirmation_workflow import apply_confirmation_decision, load_confirmation_state


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply PeerAssist human confirmation decisions.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--paper-id", default="")
    parser.add_argument("--concern-id")
    parser.add_argument("--action", choices=["confirm", "rewrite", "downgrade", "delete", "mark_pending"])
    parser.add_argument("--reviewer-id", default="local-reviewer")
    parser.add_argument("--timestamp", default="")
    parser.add_argument("--previous-text", default="")
    parser.add_argument("--new-text", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--state", action="store_true", help="Print current confirmation queue state.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if args.state:
        print(json.dumps(load_confirmation_state(run_dir=run_dir), ensure_ascii=False, indent=2))
        return 0
    if not args.paper_id or not args.concern_id or not args.action or not args.timestamp:
        parser.error("--paper-id, --concern-id, --action, and --timestamp are required unless --state is used")
    result = apply_confirmation_decision(
        run_dir=run_dir,
        paper_id=args.paper_id,
        concern_id=args.concern_id,
        action=args.action,
        reviewer_id=args.reviewer_id,
        timestamp=args.timestamp,
        previous_text=args.previous_text,
        new_text=args.new_text,
        reason=args.reason,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
