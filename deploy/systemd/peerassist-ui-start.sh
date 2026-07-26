#!/bin/sh
set -eu

app_dir=/root/PeerAssist
run_dir=/root/PeerAssist/runtime/runs/arxiv_real_data/runs/arxiv_2607_08522_v1

export PEERASSIST_PLATFORM_API_URL="${PEERASSIST_PLATFORM_API_URL:-http://127.0.0.1:8000}"
export PEERASSIST_IDENTITY_GATEWAY_URL="${PEERASSIST_IDENTITY_GATEWAY_URL:-http://127.0.0.1:8001}"

exec "$app_dir/.venv/bin/python" -m peerassist.confirmation_server \
  --run-dir "$run_dir" \
  --paper-id arxiv_2607_08522_v1 \
  --host 0.0.0.0 \
  --port 8766
