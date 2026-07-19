#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ENV_DIR=$(mktemp -d)
chmod 0700 "$ENV_DIR"
ENV_FILE="$ENV_DIR/environment"

cleanup() {
  "$ROOT/scripts/m1_reference_env.sh" down "$ENV_FILE" >/dev/null 2>&1 || true
  rmdir -- "$ENV_DIR" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cd "$ROOT"
"$ROOT/scripts/m1_reference_env.sh" create "$ENV_FILE"
"$ROOT/scripts/m1_reference_env.sh" up "$ENV_FILE"
"$ROOT/scripts/m1_reference_env.sh" wait "$ENV_FILE"

set -a
source "$ENV_FILE"
set +a
export PEERASSIST_REFERENCE_ENV_FILE="$ENV_FILE"
export PYTHONPATH=src:.

.venv/bin/python -m pytest -q -m requires_docker tests/platform/system

printf '%s\n' "M1 reference Compose smoke passed"
