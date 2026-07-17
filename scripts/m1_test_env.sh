#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
COMPOSE_FILE="$ROOT/infrastructure/compose/compose.m1.test.yml"
FORMAT=peerassist-m1-test-v1
REQUIRED_NAMES=(
  M1_TEST_ENV_FORMAT M1_TEST_CLEANUP_ID M1_TEST_PROJECT_NAME
  M1_TEST_DATABASE_URL M1_TEST_OIDC_ISSUER M1_TEST_PUBLIC_ORIGIN
  M1_TEST_OIDC_REDIRECT_URI M1_TEST_S3_ENDPOINT
  M1_TEST_POSTGRES_USER M1_TEST_POSTGRES_PASSWORD M1_TEST_POSTGRES_DB
  M1_TEST_POSTGRES_PORT M1_TEST_KEYCLOAK_PORT M1_TEST_MINIO_PORT
  M1_TEST_API_CALLBACK_PORT
  M1_TEST_KEYCLOAK_ADMIN M1_TEST_KEYCLOAK_ADMIN_PASSWORD
  M1_TEST_OIDC_REALM M1_TEST_OIDC_AUTOMATION_CLIENT_ID
  M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET M1_TEST_OIDC_PKCE_CLIENT_ID
  M1_TEST_OIDC_USERNAME M1_TEST_OIDC_USER_PASSWORD
  M1_TEST_S3_ACCESS_KEY M1_TEST_S3_SECRET_KEY M1_TEST_MINIO_ROOT_PASSWORD
  M1_TEST_S3_BUCKET M1_TEST_S3_REGION
)
SERVICES=(postgres keycloak minio minio-bootstrap)

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

random_hex() {
  python3 -c 'import secrets; print(secrets.token_hex(int(__import__("sys").argv[1])))' "$1"
}

free_ports() {
  python3 - <<'PY'
import socket

sockets = []
try:
    for _ in range(4):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    print(" ".join(str(sock.getsockname()[1]) for sock in sockets))
finally:
    for sock in sockets:
        sock.close()
PY
}

create_env() {
  local env_file=$1 dir base temp cleanup_id project
  [[ ! -e "$env_file" && ! -L "$env_file" ]] || fail "environment target already exists"
  dir=$(dirname -- "$env_file")
  base=$(basename -- "$env_file")
  [[ -d "$dir" && ! -L "$dir" ]] || fail "environment parent must be an existing real directory"
  temp=$(mktemp -- "$dir/.m1-test-env.XXXXXXXX")
  trap 'rm -f -- "$temp"' RETURN
  chmod 0600 "$temp"

  cleanup_id=$(random_hex 12)
  project="peerassist-m1-$cleanup_id"
  local pg_credential kc_credential client_credential user_credential
  local s3_access s3_credential root_credential bucket
  local postgres_port keycloak_port minio_port api_callback_port
  pg_credential=$(random_hex 24)
  kc_credential=$(random_hex 24)
  client_credential=$(random_hex 24)
  user_credential=$(random_hex 24)
  s3_access="PA$(random_hex 9)"
  s3_credential=$(random_hex 24)
  root_credential=$s3_credential
  bucket="peerassist-m1-$cleanup_id"
  read -r postgres_port keycloak_port minio_port api_callback_port < <(free_ports)

  {
    printf 'export M1_TEST_ENV_FORMAT=%s\n' "$FORMAT"
    printf 'export M1_TEST_CLEANUP_ID=%s\n' "$cleanup_id"
    printf 'export M1_TEST_PROJECT_NAME=%s\n' "$project"
    printf 'export M1_TEST_POSTGRES_USER=%s\n' peerassist
    printf 'export %s=%s\n' M1_TEST_POSTGRES_PASSWORD "$pg_credential"
    printf 'export M1_TEST_POSTGRES_DB=%s\n' peerassist
    printf 'export M1_TEST_POSTGRES_PORT=%s\n' "$postgres_port"
    printf 'export M1_TEST_DATABASE_URL=postgresql://peerassist:%s@127.0.0.1:%s/peerassist\n' \
      "$pg_credential" "$postgres_port"
    printf 'export M1_TEST_KEYCLOAK_ADMIN=%s\n' m1admin
    printf 'export %s=%s\n' M1_TEST_KEYCLOAK_ADMIN_PASSWORD "$kc_credential"
    printf 'export M1_TEST_OIDC_REALM=%s\n' peerassist-m1
    printf 'export M1_TEST_OIDC_AUTOMATION_CLIENT_ID=%s\n' peerassist-automation
    printf 'export M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET=%s\n' "$client_credential"
    printf 'export M1_TEST_OIDC_PKCE_CLIENT_ID=%s\n' peerassist-browser
    printf 'export M1_TEST_OIDC_USERNAME=%s\n' org-a-admin
    printf 'export %s=%s\n' M1_TEST_OIDC_USER_PASSWORD "$user_credential"
    printf 'export M1_TEST_KEYCLOAK_PORT=%s\n' "$keycloak_port"
    printf 'export M1_TEST_OIDC_ISSUER=http://127.0.0.1:%s/realms/peerassist-m1\n' "$keycloak_port"
    printf 'export M1_TEST_API_CALLBACK_PORT=%s\n' "$api_callback_port"
    printf 'export M1_TEST_PUBLIC_ORIGIN=http://127.0.0.1:%s\n' "$api_callback_port"
    printf 'export M1_TEST_OIDC_REDIRECT_URI=http://127.0.0.1:%s/api/v1/auth/callback\n' \
      "$api_callback_port"
    printf 'export M1_TEST_S3_ACCESS_KEY=%s\n' "$s3_access"
    printf 'export M1_TEST_S3_SECRET_KEY=%s\n' "$s3_credential"
    printf 'export %s=%s\n' M1_TEST_MINIO_ROOT_PASSWORD "$root_credential"
    printf 'export M1_TEST_S3_BUCKET=%s\n' "$bucket"
    printf 'export M1_TEST_S3_REGION=%s\n' us-east-1
    printf 'export M1_TEST_MINIO_PORT=%s\n' "$minio_port"
    printf 'export M1_TEST_S3_ENDPOINT=http://127.0.0.1:%s\n' "$minio_port"
  } > "$temp"

  # A same-directory hard link makes the fully written file visible atomically
  # and fails closed if another process wins the target-name race.
  ln -- "$temp" "$dir/$base" || fail "environment target already exists"
  rm -f -- "$temp"
  trap - RETURN
}

allowed_name() {
  local candidate=$1 allowed
  for allowed in "${REQUIRED_NAMES[@]}"; do
    [[ "$candidate" == "$allowed" ]] && return 0
  done
  return 1
}

load_env() {
  local env_file=$1 mode owner line key value expected
  [[ -f "$env_file" && ! -L "$env_file" ]] || fail "environment must be a regular non-symlink file"
  mode=$(stat -c '%a' -- "$env_file")
  [[ "$mode" == 600 ]] || fail "environment file must have mode 0600"
  owner=$(stat -c '%u' -- "$env_file")
  [[ "$owner" == "$(id -u)" ]] || fail "environment file must be owned by the caller"

  declare -A seen=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^export\ (M1_TEST_[A-Z0-9_]+)=([A-Za-z0-9_./:@-]+)$ ]] || \
      fail "unsafe generated environment content"
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    allowed_name "$key" || fail "unsafe generated environment variable"
    [[ -z "${seen[$key]+set}" ]] || fail "duplicate generated environment variable"
    seen[$key]=1
    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$env_file"
  for expected in "${REQUIRED_NAMES[@]}"; do
    [[ -n "${seen[$expected]+set}" ]] || fail "incomplete generated environment"
  done

  [[ "$M1_TEST_ENV_FORMAT" == "$FORMAT" ]] || fail "unsafe generated environment format"
  [[ "$M1_TEST_CLEANUP_ID" =~ ^[0-9a-f]{24}$ ]] || fail "unsafe cleanup identifier"
  [[ "$M1_TEST_PROJECT_NAME" == "peerassist-m1-$M1_TEST_CLEANUP_ID" ]] || \
    fail "unsafe Compose project name"
  [[ "$M1_TEST_POSTGRES_PORT" =~ ^[0-9]{4,5}$ ]] || fail "unsafe PostgreSQL port"
  [[ "$M1_TEST_KEYCLOAK_PORT" =~ ^[0-9]{4,5}$ ]] || fail "unsafe Keycloak port"
  [[ "$M1_TEST_MINIO_PORT" =~ ^[0-9]{4,5}$ ]] || fail "unsafe MinIO port"
  [[ "$M1_TEST_API_CALLBACK_PORT" =~ ^[0-9]{4,5}$ ]] || fail "unsafe API callback port"
  [[ "$M1_TEST_DATABASE_URL" == \
    "postgresql://$M1_TEST_POSTGRES_USER:$M1_TEST_POSTGRES_PASSWORD@127.0.0.1:$M1_TEST_POSTGRES_PORT/$M1_TEST_POSTGRES_DB" ]] || \
    fail "unsafe generated database URL"
  [[ "$M1_TEST_OIDC_ISSUER" == \
    "http://127.0.0.1:$M1_TEST_KEYCLOAK_PORT/realms/$M1_TEST_OIDC_REALM" ]] || \
    fail "unsafe generated OIDC issuer"
  [[ "$M1_TEST_PUBLIC_ORIGIN" == "http://127.0.0.1:$M1_TEST_API_CALLBACK_PORT" ]] || \
    fail "unsafe generated public origin"
  [[ "$M1_TEST_OIDC_REDIRECT_URI" == \
    "$M1_TEST_PUBLIC_ORIGIN/api/v1/auth/callback" ]] || \
    fail "unsafe generated OIDC redirect URI"
  [[ "$M1_TEST_S3_ENDPOINT" == "http://127.0.0.1:$M1_TEST_MINIO_PORT" ]] || \
    fail "unsafe generated S3 endpoint"
  [[ ${#M1_TEST_POSTGRES_PASSWORD} -ge 32 ]] || fail "unsafe generated PostgreSQL credential"
  [[ ${#M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET} -ge 32 ]] || fail "unsafe generated OIDC credential"
  [[ ${#M1_TEST_S3_SECRET_KEY} -ge 32 ]] || fail "unsafe generated S3 credential"
  [[ "$M1_TEST_MINIO_ROOT_PASSWORD" == "$M1_TEST_S3_SECRET_KEY" ]] || \
    fail "inconsistent generated MinIO credential"
}

validate_services() {
  local requested known valid
  for requested in "$@"; do
    valid=false
    for known in "${SERVICES[@]}"; do
      [[ "$requested" == "$known" ]] && valid=true
    done
    [[ "$valid" == true ]] || fail "unknown M1 test service"
  done
}

compose() {
  docker compose --project-name "$M1_TEST_PROJECT_NAME" \
    --env-file "$1" -f "$COMPOSE_FILE" "${@:2}"
}

up_env() {
  local env_file=$1
  shift
  load_env "$env_file"
  validate_services "$@"
  compose "$env_file" up --detach "$@"
}

wait_env() {
  local env_file=$1
  shift
  load_env "$env_file"
  if (( $# == 0 )); then
    set -- "${SERVICES[@]}"
  else
    validate_services "$@"
  fi
  local deadline=$((SECONDS + 180)) service cid state all_ready
  while (( SECONDS < deadline )); do
    all_ready=true
    for service in "$@"; do
      cid=$(compose "$env_file" ps --all --quiet "$service")
      if [[ -z "$cid" ]]; then
        all_ready=false
        continue
      fi
      state=$(docker inspect --format \
        '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.State.ExitCode}}' \
        "$cid")
      case "$service:$state" in
        minio-bootstrap:"exited  0"|*:"running healthy 0") ;;
        *:"exited "*|*:"dead "*) fail "M1 test provider failed before readiness" ;;
        *) all_ready=false ;;
      esac
    done
    [[ "$all_ready" == true ]] && return 0
    sleep 1
  done
  fail "timed out waiting for M1 test providers"
}

down_env() {
  local env_file=$1 status=0
  [[ -e "$env_file" || -L "$env_file" ]] || return 0
  load_env "$env_file"
  compose "$env_file" down --volumes --remove-orphans || status=$?
  rm -f -- "$env_file"
  return "$status"
}

usage() {
  printf 'usage: %s {create|up|wait|down} <environment-file> [services...]\n' \
    "${0##*/}" >&2
  exit 2
}

(( $# >= 2 )) || usage
command=$1
env_file=$2
shift 2
case "$command" in
  create) (( $# == 0 )) || usage; create_env "$env_file" ;;
  up) up_env "$env_file" "$@" ;;
  wait) wait_env "$env_file" "$@" ;;
  down) (( $# == 0 )) || usage; down_env "$env_file" ;;
  *) usage ;;
esac
