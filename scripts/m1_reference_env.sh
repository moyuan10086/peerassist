#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
COMPOSE_FILE="$ROOT/infrastructure/compose/compose.m1.yml"

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

random_hex() {
  python3 -c 'import secrets; print(secrets.token_hex(int(__import__("sys").argv[1])))' "$1"
}

load_env() {
  local env_file=$1 line key value
  [[ -f "$env_file" && ! -L "$env_file" ]] || fail "reference environment is unavailable"
  [[ "$(stat -c '%a' -- "$env_file")" == 600 ]] || fail "reference environment must use mode 0600"
  [[ "$(stat -c '%u' -- "$env_file")" == "$(id -u)" ]] || fail "reference environment owner is invalid"
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^([A-Z0-9_]+)=([A-Za-z0-9_./:@=-]+)$ ]] || fail "unsafe reference environment content"
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    [[ "$key" == PEERASSIST_* ]] || fail "unsafe reference environment variable"
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$env_file"
  [[ "${PEERASSIST_REFERENCE_FORMAT-}" == peerassist-m1-reference-v1 ]] || fail "reference environment format is invalid"
  [[ "${PEERASSIST_REFERENCE_PROJECT-}" =~ ^peerassist-m1-ref-[0-9a-f]{16}$ ]] || fail "reference project is invalid"
  [[ "${PEERASSIST_M1_PORT-}" =~ ^[1-9][0-9]{3,4}$ ]] || fail "reference port is invalid"
  [[ "${PEERASSIST_PUBLIC_ORIGIN-}" =~ ^http://[A-Za-z0-9.-]+:$PEERASSIST_M1_PORT$ ]] || fail "reference origin is invalid"
}

compose() {
  docker compose --project-name "$PEERASSIST_REFERENCE_PROJECT" \
    --env-file "$1" -f "$COMPOSE_FILE" "${@:2}"
}

create_env() {
  local env_file=$1 parent token state legacy port temp
  parent=$(realpath -e -- "$(dirname -- "$env_file")") || fail "reference environment parent is invalid"
  [[ ! -e "$env_file" && ! -L "$env_file" ]] || fail "reference environment already exists"
  token=$(random_hex 8)
  state="$parent/peerassist-m1-state-$token"
  legacy="$parent/peerassist-m1-legacy-$token"
  install -d -m 0755 "$state" "$legacy"
  if [[ "$(id -u)" == 0 ]]; then
    chown 10001:10002 "$state"
  else
    chmod 0777 "$state"
  fi
  port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
  temp=$(mktemp -- "$parent/.m1-reference.XXXXXXXX")
  chmod 0600 "$temp"
  {
    printf 'PEERASSIST_REFERENCE_FORMAT=peerassist-m1-reference-v1\n'
    printf 'PEERASSIST_REFERENCE_PROJECT=peerassist-m1-ref-%s\n' "$token"
    printf 'PEERASSIST_M1_PORT=%s\n' "$port"
    printf 'PEERASSIST_PUBLIC_ORIGIN=http://127.0.0.1:%s\n' "$port"
    printf 'PEERASSIST_DB_USER=peerassist\n'
    printf 'PEERASSIST_DB_PASSWORD=%s\n' "$(random_hex 24)"
    printf 'PEERASSIST_DB_NAME=peerassist\n'
    printf 'PEERASSIST_KEYCLOAK_ADMIN=m1admin\n'
    printf 'PEERASSIST_KEYCLOAK_ADMIN_PASSWORD=%s\n' "$(random_hex 24)"
    printf 'PEERASSIST_OIDC_REALM=peerassist-m1\n'
    printf 'PEERASSIST_OIDC_AUTOMATION_CLIENT_ID=peerassist-automation\n'
    printf 'PEERASSIST_OIDC_AUTOMATION_CLIENT_SECRET=%s\n' "$(random_hex 24)"
    printf 'PEERASSIST_OIDC_CLIENT_ID=peerassist-browser\n'
    printf 'PEERASSIST_OIDC_USERNAME=org-a-admin\n'
    printf 'PEERASSIST_OIDC_USER_PASSWORD=%s\n' "$(random_hex 24)"
    printf 'PEERASSIST_S3_ACCESS_KEY=PA%s\n' "$(random_hex 9)"
    printf 'PEERASSIST_S3_SECRET_KEY=%s\n' "$(random_hex 24)"
    printf 'PEERASSIST_S3_BUCKET=peerassist-m1-%s\n' "$token"
    printf 'PEERASSIST_SESSION_KEY=m1=%s\n' "$(random_hex 32)"
    printf 'PEERASSIST_BOOTSTRAP_OPERATOR_ID=%s\n' "$(python3 -c 'import uuid; print(uuid.uuid4())')"
    printf 'PEERASSIST_BOOTSTRAP_STATE_DIR=%s\n' "$state"
    printf 'PEERASSIST_LEGACY_ROOT=%s\n' "$legacy"
  } > "$temp"
  mv -- "$temp" "$env_file"
}

up_env() {
  local env_file=$1
  load_env "$env_file"
  compose "$env_file" up --build --detach
}

wait_env() {
  local env_file=$1 deadline status
  load_env "$env_file"
  deadline=$((SECONDS + 420))
  while (( SECONDS < deadline )); do
    status=$(compose "$env_file" ps --format json 2>/dev/null || true)
    if curl --fail --silent --max-time 3 "$PEERASSIST_PUBLIC_ORIGIN/api/v1/ready" >/dev/null 2>&1 \
      && [[ -f "$PEERASSIST_BOOTSTRAP_STATE_DIR/scope.json" ]] \
      && compose "$env_file" ps --status running --quiet worker | grep -q .; then
      return 0
    fi
    if compose "$env_file" ps --all --format '{{.Service}} {{.State}} {{.ExitCode}}' | \
      grep -Eq '^(migrate|platform-bootstrap|minio-bootstrap) exited [1-9]'; then
      printf '%s\n' "$status" >&2
      fail "reference bootstrap service failed"
    fi
    sleep 2
  done
  printf '%s\n' "$status" >&2
  fail "timed out waiting for M1 reference profile"
}

down_env() {
  local env_file=$1 residual state legacy
  [[ -e "$env_file" ]] || return 0
  load_env "$env_file"
  state=$PEERASSIST_BOOTSTRAP_STATE_DIR
  legacy=$PEERASSIST_LEGACY_ROOT
  compose "$env_file" down --volumes --remove-orphans
  residual=$(docker ps -aq --filter "label=com.docker.compose.project=$PEERASSIST_REFERENCE_PROJECT")
  [[ -z "$residual" ]] || fail "reference containers remain after cleanup"
  residual=$(docker volume ls -q --filter "label=com.docker.compose.project=$PEERASSIST_REFERENCE_PROJECT")
  [[ -z "$residual" ]] || fail "reference volumes remain after cleanup"
  find "$state" "$legacy" -mindepth 1 -maxdepth 1 -type f -delete
  rmdir -- "$state" "$legacy"
  rm -f -- "$env_file"
}

usage() {
  printf 'usage: %s {create|up|wait|down} <environment-file>\n' "${0##*/}" >&2
  exit 2
}

(( $# == 2 )) || usage
command=$1
env_file=$2
case "$command" in
  create) create_env "$env_file" ;;
  up) up_env "$env_file" ;;
  wait) wait_env "$env_file" ;;
  down) down_env "$env_file" ;;
  *) usage ;;
esac
