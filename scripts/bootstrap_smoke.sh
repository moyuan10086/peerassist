#!/usr/bin/env bash
set -Eeuo pipefail

PDF_PATH="demos/Text/bert/paper.pdf"
COMPOSE_FILE="infrastructure/compose/compose.yml"

if [[ "${PEERASSIST_BOOTSTRAP_INNER:-}" != "1" ]]; then
  source_root="$(git rev-parse --show-toplevel)"
  commit="$(git -C "$source_root" rev-parse HEAD)"
  temp_root="$(mktemp -d)"
  test -z "$(git -C "$source_root" status --porcelain)"
  lfs_pointer="$(git -C "$source_root" show "$commit:$PDF_PATH")"
  expected_oid="$(printf '%s\n' "$lfs_pointer" | sed -n 's/^oid sha256:\([0-9a-f]\{64\}\)$/\1/p')"
  [[ "$expected_oid" =~ ^[0-9a-f]{64}$ ]]

  cleanup() {
    rm -rf "$temp_root"
  }
  trap cleanup EXIT

  GIT_LFS_SKIP_SMUDGE=1 git clone --local --no-hardlinks "$source_root" "$temp_root/repo"
  (
    cd "$temp_root/repo"
    git lfs version >/dev/null
    git lfs fetch origin "$commit" --include=demos/Text/bert/paper.pdf
    git lfs checkout demos/Text/bert/paper.pdf
    test "$(sha256sum "$PDF_PATH" | awk '{print $1}')" = "$expected_oid"
  )
  PEERASSIST_BOOTSTRAP_INNER=1 bash "$temp_root/repo/scripts/bootstrap_smoke.sh"
  exit 0
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
export COMPOSE_PROJECT_NAME="peerassist-smoke-${GITHUB_RUN_ID:-$$}-${GITHUB_RUN_ATTEMPT:-0}"
export PEERASSIST_WORKSPACE_BIND_PORT=0
response_headers="$(mktemp)"
response_body="$(mktemp)"
expected_body="$(mktemp)"

cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v >/dev/null 2>&1 || true
  rm -f "$response_headers" "$response_body" "$expected_body"
}
trap cleanup EXIT

test -z "$(git status --porcelain)"
test "$(head -c 4 demos/Text/bert/paper.pdf)" = "%PDF"

python -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple '.[runtime,positioning,refcheck,dev]'
npm ci --prefix web/peerassist-workspace
source .venv/bin/activate
python scripts/verify_repository.py all

docker compose -f infrastructure/compose/compose.yml up -d --build
workspace_address="$(docker compose -f "$COMPOSE_FILE" port workspace 8766)"
healthy=0
for _ in $(seq 1 60); do
  running_services="$(docker compose -f "$COMPOSE_FILE" ps --status running --services)"
  if grep -Fxq "review-api" <<<"$running_services" \
    && grep -Fxq "workspace" <<<"$running_services" \
    && curl --fail --silent "http://$workspace_address/api/health" >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
test "$healthy" -eq 1

curl --silent --show-error --fail \
  --range 0-1023 \
  --dump-header "$response_headers" \
  --output "$response_body" \
  "http://$workspace_address/paper.pdf"
grep -Eq '^HTTP/[^ ]+ 206([[:space:]]|$)' "$response_headers"
grep -Eqi '^Content-Range: bytes 0-1023/' "$response_headers"
test "$(wc -c < "$response_body")" -eq 1024
head -c 1024 "$PDF_PATH" > "$expected_body"
cmp "$expected_body" "$response_body"
test -z "$(git status --porcelain)"
