#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ENV_FILE=$(mktemp)
chmod 0600 "$ENV_FILE"

cleanup() {
  "$ROOT/scripts/m1_test_env.sh" down "$ENV_FILE" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cd "$ROOT"
"$ROOT/scripts/m1_test_env.sh" create "$ENV_FILE"
"$ROOT/scripts/m1_test_env.sh" up "$ENV_FILE" postgres keycloak minio-bootstrap
source "$ENV_FILE"
"$ROOT/scripts/m1_test_env.sh" require-ready "$ENV_FILE"
"$ROOT/scripts/m1_test_env.sh" wait "$ENV_FILE" postgres keycloak minio minio-bootstrap

export PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL"
export PEERASSIST_TEST_S3_ENDPOINT="$M1_TEST_S3_ENDPOINT"
export PYTHONPATH=src:.

.venv/bin/python -m pytest -q -m requires_docker \
  tests/platform/integration/test_postgres_review_commands.py
.venv/bin/python -m pytest -q -m requires_docker \
  tests/platform/contracts/test_object_store_contract.py --adapter=minio
.venv/bin/python -m pytest -q -m requires_docker \
  tests/platform/integration/test_minio_object_contract.py
.venv/bin/python -m pytest -q -m requires_docker \
  tests/platform/integration/test_keycloak_identity_contract.py

printf '%s\n' "M1 provider smoke passed"
