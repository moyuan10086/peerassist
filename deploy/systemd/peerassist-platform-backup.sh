#!/bin/sh
set -eu

umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root to back up the PeerAssist platform" >&2
  exit 2
fi

app_dir=${PEERASSIST_APP_DIR:-/root/PeerAssist}
env_file=${PEERASSIST_PLATFORM_ENV_FILE:-/etc/peerassist/platform.env}
project=${PEERASSIST_PLATFORM_PROJECT:-peerassist-platform}
backup_root=${PEERASSIST_BACKUP_ROOT:-/var/backups/peerassist}
caddyfile=${PEERASSIST_CADDYFILE:-/opt/vaultwarden/Caddyfile}
base_compose=$app_dir/infrastructure/compose/compose.m1.yml
platform_compose=$app_dir/infrastructure/compose/compose.platform.yml

[ -d "$app_dir/.git" ] || { echo "PeerAssist repository is unavailable" >&2; exit 2; }
[ -f "$env_file" ] || { echo "PeerAssist platform environment is unavailable" >&2; exit 2; }
[ "$(stat -c '%a' "$env_file")" = 600 ] || {
  echo "PeerAssist platform environment must use mode 0600" >&2
  exit 2
}

# The environment file is root-owned mode 0600 and supplies external volume names.
# shellcheck disable=SC1090
. "$env_file"
: "${PEERASSIST_POSTGRES_VOLUME:?missing PostgreSQL volume name}"
: "${PEERASSIST_KEYCLOAK_VOLUME:?missing Keycloak volume name}"
: "${PEERASSIST_MINIO_VOLUME:?missing MinIO volume name}"
: "${PEERASSIST_WORKER_SCRATCH_VOLUME:?missing worker volume name}"
: "${PEERASSIST_MODEL_SETTINGS_VOLUME:?missing model settings volume name}"

compose() {
  docker compose --project-name "$project" --env-file "$env_file"     -f "$base_compose" -f "$platform_compose" "$@"
}

services_stopped=0
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$services_stopped" -eq 1 ]; then
    compose start keycloak minio api worker >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

snapshot_volume() {
  volume=$1
  archive=$2
  docker run --rm --read-only --network none --cap-drop ALL --cap-add DAC_READ_SEARCH     -v "$volume:/source:ro" -v "$bundle:/backup" alpine:3.22.0     tar -C /source -czf "/backup/$archive" .
}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
bundle=$backup_root/platform-$stamp
[ ! -e "$bundle" ] || { echo "backup already exists: $bundle" >&2; exit 2; }
install -d -m 0700 "$backup_root" "$bundle"
install -m 0600 "$env_file" "$bundle/platform.env"
if [ -f "$caddyfile" ]; then
  install -m 0600 "$caddyfile" "$bundle/Caddyfile"
fi

compose exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"'   >"$bundle/postgres.dump"

compose stop keycloak minio api worker
services_stopped=1
snapshot_volume "$PEERASSIST_KEYCLOAK_VOLUME" keycloak-data.tgz
snapshot_volume "$PEERASSIST_MINIO_VOLUME" minio-data.tgz
snapshot_volume "$PEERASSIST_MODEL_SETTINGS_VOLUME" model-settings-data.tgz
snapshot_volume "$PEERASSIST_WORKER_SCRATCH_VOLUME" worker-scratch.tgz
compose start keycloak minio api worker >/dev/null
services_stopped=0

ready_deadline=$(( $(date +%s) + 420 ))
while [ "$(date +%s)" -lt "$ready_deadline" ]; do
  if curl --fail --silent --max-time 3       http://127.0.0.1:8000/api/v1/ready >/dev/null 2>&1     && [ -n "$(compose ps --status running --quiet worker)" ]; then
    break
  fi
  sleep 2
done
curl --fail --silent --max-time 3   http://127.0.0.1:8000/api/v1/ready >/dev/null
[ -n "$(compose ps --status running --quiet worker)" ]

(
  cd "$bundle"
  find . -maxdepth 1 -type f ! -name SHA256SUMS -printf '%P\0'     | sort -z | xargs -0 sha256sum >SHA256SUMS
  sha256sum -c SHA256SUMS >/dev/null
)
chmod 0600 "$bundle"/*
trap - EXIT HUP INT TERM
printf '%s\n' "$bundle"
