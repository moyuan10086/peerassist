#!/usr/bin/env python3
"""Export or check the canonical PeerAssist v1 OpenAPI contract."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from services.api.app import create_openapi_app

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi" / "peerassist-v1.json"


def canonical_openapi() -> bytes:
    schema = create_openapi_app().openapi()
    return (json.dumps(schema, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def export_contract(content: bytes) -> None:
    """Atomically replace the contract after durable sibling-tempfile publication."""

    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=CONTRACT.parent,
            prefix=f".{CONTRACT.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, CONTRACT)
        temporary_path = None
        directory_fd = os.open(CONTRACT.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when the committed contract differs")
    args = parser.parse_args()
    expected = canonical_openapi()
    if args.check:
        if not CONTRACT.exists() or CONTRACT.read_bytes() != expected:
            print("OpenAPI contract is out of date; run scripts/export_openapi.py")
            return 1
        return 0
    export_contract(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
