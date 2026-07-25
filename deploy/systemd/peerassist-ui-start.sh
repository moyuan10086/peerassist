#!/bin/sh
set -eu

app_dir=/root/PeerAssist/current
run_dir=/root/PeerAssist/runtime/runs/arxiv_real_data/runs/arxiv_2607_08522_v1

keycloak_container="$(docker ps \
  --filter label=com.docker.compose.service=keycloak \
  --format '{{.ID}}' | head -n 1)"
if [ -z "$keycloak_container" ]; then
  echo "PeerAssist identity container is not running" >&2
  exit 1
fi

keycloak_ip="$(docker inspect \
  -f '{{range .NetworkSettings.Networks}}{{println .IPAddress}}{{end}}' \
  "$keycloak_container" | head -n 1)"
if [ -z "$keycloak_ip" ]; then
  echo "PeerAssist identity container has no network address" >&2
  exit 1
fi

export PEERASSIST_PLATFORM_API_URL="${PEERASSIST_PLATFORM_API_URL:-http://127.0.0.1:8000}"
export PEERASSIST_IDENTITY_GATEWAY_URL="http://${keycloak_ip}:8080"

exec "$app_dir/.venv/bin/python" -m peerassist.confirmation_server \
  --run-dir "$run_dir" \
  --paper-id arxiv_2607_08522_v1 \
  --host 0.0.0.0 \
  --port 8766
