#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
COMPOSE_FILE="$ROOT/infrastructure/compose/compose.m1.test.yml"
FORMAT=peerassist-m1-test-v1
REQUIRED_NAMES=(
  M1_TEST_ENV_FORMAT M1_TEST_CLEANUP_ID M1_TEST_PROJECT_NAME
  M1_TEST_ENDPOINTS_READY
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
SERVICES=(postgres keycloak minio minio-bootstrap callback-reservation)

fail() {
  printf '%s\n' "$1" >&2
  exit 2
}

random_hex() {
  python3 -c 'import secrets; print(secrets.token_hex(int(__import__("sys").argv[1])))' "$1"
}

emit_env() {
  printf 'export M1_TEST_ENV_FORMAT=%s\n' "$FORMAT"
  printf 'export M1_TEST_CLEANUP_ID=%s\n' "$M1_TEST_CLEANUP_ID"
  printf 'export M1_TEST_PROJECT_NAME=%s\n' "$M1_TEST_PROJECT_NAME"
  printf 'export M1_TEST_ENDPOINTS_READY=%s\n' "$M1_TEST_ENDPOINTS_READY"
  printf 'export M1_TEST_POSTGRES_USER=%s\n' "$M1_TEST_POSTGRES_USER"
  printf 'export %s=%s\n' M1_TEST_POSTGRES_PASSWORD "$M1_TEST_POSTGRES_PASSWORD"
  printf 'export M1_TEST_POSTGRES_DB=%s\n' "$M1_TEST_POSTGRES_DB"
  printf 'export M1_TEST_POSTGRES_PORT=%s\n' "$M1_TEST_POSTGRES_PORT"
  printf 'export M1_TEST_DATABASE_URL=postgresql://%s:%s@127.0.0.1:%s/%s\n' \
    "$M1_TEST_POSTGRES_USER" "$M1_TEST_POSTGRES_PASSWORD" \
    "$M1_TEST_POSTGRES_PORT" "$M1_TEST_POSTGRES_DB"
  printf 'export M1_TEST_KEYCLOAK_ADMIN=%s\n' "$M1_TEST_KEYCLOAK_ADMIN"
  printf 'export %s=%s\n' M1_TEST_KEYCLOAK_ADMIN_PASSWORD \
    "$M1_TEST_KEYCLOAK_ADMIN_PASSWORD"
  printf 'export M1_TEST_OIDC_REALM=%s\n' "$M1_TEST_OIDC_REALM"
  printf 'export M1_TEST_OIDC_AUTOMATION_CLIENT_ID=%s\n' \
    "$M1_TEST_OIDC_AUTOMATION_CLIENT_ID"
  printf 'export M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET=%s\n' \
    "$M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET"
  printf 'export M1_TEST_OIDC_PKCE_CLIENT_ID=%s\n' "$M1_TEST_OIDC_PKCE_CLIENT_ID"
  printf 'export M1_TEST_OIDC_USERNAME=%s\n' "$M1_TEST_OIDC_USERNAME"
  printf 'export %s=%s\n' M1_TEST_OIDC_USER_PASSWORD "$M1_TEST_OIDC_USER_PASSWORD"
  printf 'export M1_TEST_KEYCLOAK_PORT=%s\n' "$M1_TEST_KEYCLOAK_PORT"
  printf 'export M1_TEST_OIDC_ISSUER=http://127.0.0.1:%s/realms/%s\n' \
    "$M1_TEST_KEYCLOAK_PORT" "$M1_TEST_OIDC_REALM"
  printf 'export M1_TEST_API_CALLBACK_PORT=%s\n' "$M1_TEST_API_CALLBACK_PORT"
  printf 'export M1_TEST_PUBLIC_ORIGIN=http://127.0.0.1:%s\n' \
    "$M1_TEST_API_CALLBACK_PORT"
  printf 'export M1_TEST_OIDC_REDIRECT_URI=http://127.0.0.1:%s/api/v1/auth/callback\n' \
    "$M1_TEST_API_CALLBACK_PORT"
  printf 'export M1_TEST_S3_ACCESS_KEY=%s\n' "$M1_TEST_S3_ACCESS_KEY"
  printf 'export M1_TEST_S3_SECRET_KEY=%s\n' "$M1_TEST_S3_SECRET_KEY"
  printf 'export %s=%s\n' M1_TEST_MINIO_ROOT_PASSWORD "$M1_TEST_MINIO_ROOT_PASSWORD"
  printf 'export M1_TEST_S3_BUCKET=%s\n' "$M1_TEST_S3_BUCKET"
  printf 'export M1_TEST_S3_REGION=%s\n' "$M1_TEST_S3_REGION"
  printf 'export M1_TEST_MINIO_PORT=%s\n' "$M1_TEST_MINIO_PORT"
  printf 'export M1_TEST_S3_ENDPOINT=http://127.0.0.1:%s\n' "$M1_TEST_MINIO_PORT"
}

persist_env() {
  local env_file=$1 dir temp
  dir=$(dirname -- "$env_file")
  temp=$(mktemp -- "$dir/.m1-test-env.XXXXXXXX")
  trap 'rm -f -- "$temp"' RETURN
  chmod 0600 "$temp"
  emit_env > "$temp"
  mv -f -- "$temp" "$env_file"
  trap - RETURN
}

refresh_endpoints() {
  M1_TEST_DATABASE_URL="postgresql://$M1_TEST_POSTGRES_USER:$M1_TEST_POSTGRES_PASSWORD@127.0.0.1:$M1_TEST_POSTGRES_PORT/$M1_TEST_POSTGRES_DB"
  M1_TEST_OIDC_ISSUER="http://127.0.0.1:$M1_TEST_KEYCLOAK_PORT/realms/$M1_TEST_OIDC_REALM"
  M1_TEST_PUBLIC_ORIGIN="http://127.0.0.1:$M1_TEST_API_CALLBACK_PORT"
  M1_TEST_OIDC_REDIRECT_URI="$M1_TEST_PUBLIC_ORIGIN/api/v1/auth/callback"
  M1_TEST_S3_ENDPOINT="http://127.0.0.1:$M1_TEST_MINIO_PORT"
  export M1_TEST_DATABASE_URL M1_TEST_OIDC_ISSUER M1_TEST_PUBLIC_ORIGIN
  export M1_TEST_OIDC_REDIRECT_URI M1_TEST_S3_ENDPOINT
}

create_env() {
  local env_file=$1 requested_dir dir base temp cleanup_id project
  local parent_snapshot target_snapshot replace=false
  requested_dir=$(dirname -- "$env_file")
  base=$(basename -- "$env_file")
  validate_safe_parent "$requested_dir"
  dir=$VALIDATED_PARENT_PATH
  parent_snapshot=$VALIDATED_PARENT_SNAPSHOT
  if [[ -L "$env_file" ]]; then
    fail "environment target must not be a symlink"
  elif [[ -e "$env_file" ]]; then
    validate_empty_target "$env_file"
    target_snapshot=$VALIDATED_TARGET_SNAPSHOT
    replace=true
  fi
  temp=$(mktemp -- "$dir/.m1-test-env.XXXXXXXX")
  trap 'rm -f -- "$temp"' EXIT
  chmod 0600 "$temp"

  cleanup_id=$(random_hex 12)
  project="peerassist-m1-$cleanup_id"
  local pg_credential kc_credential client_credential user_credential
  local s3_access s3_credential root_credential bucket
  pg_credential=$(random_hex 24)
  kc_credential=$(random_hex 24)
  client_credential=$(random_hex 24)
  user_credential=$(random_hex 24)
  s3_access="PA$(random_hex 9)"
  s3_credential=$(random_hex 24)
  root_credential=$s3_credential
  bucket="peerassist-m1-$cleanup_id"
  M1_TEST_ENV_FORMAT=$FORMAT
  M1_TEST_CLEANUP_ID=$cleanup_id
  M1_TEST_PROJECT_NAME=$project
  M1_TEST_ENDPOINTS_READY=0
  M1_TEST_POSTGRES_USER=peerassist
  printf -v M1_TEST_POSTGRES_PASSWORD '%s' "$pg_credential"
  M1_TEST_POSTGRES_DB=peerassist
  M1_TEST_POSTGRES_PORT=0
  M1_TEST_KEYCLOAK_ADMIN=m1admin
  printf -v M1_TEST_KEYCLOAK_ADMIN_PASSWORD '%s' "$kc_credential"
  M1_TEST_OIDC_REALM=peerassist-m1
  M1_TEST_OIDC_AUTOMATION_CLIENT_ID=peerassist-automation
  M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET=$client_credential
  M1_TEST_OIDC_PKCE_CLIENT_ID=peerassist-browser
  M1_TEST_OIDC_USERNAME=org-a-admin
  printf -v M1_TEST_OIDC_USER_PASSWORD '%s' "$user_credential"
  M1_TEST_KEYCLOAK_PORT=0
  M1_TEST_API_CALLBACK_PORT=0
  M1_TEST_S3_ACCESS_KEY=$s3_access
  M1_TEST_S3_SECRET_KEY=$s3_credential
  printf -v M1_TEST_MINIO_ROOT_PASSWORD '%s' "$root_credential"
  M1_TEST_S3_BUCKET=$bucket
  M1_TEST_S3_REGION=us-east-1
  M1_TEST_MINIO_PORT=0

  emit_env > "$temp"

  if [[ "$replace" == true ]]; then
    revalidate_parent "$requested_dir" "$dir" "$parent_snapshot"
    validate_empty_target "$dir/$base"
    [[ "$VALIDATED_TARGET_SNAPSHOT" == "$target_snapshot" ]] || \
      fail "existing environment target changed during creation"
    revalidate_parent "$requested_dir" "$dir" "$parent_snapshot"
    validate_empty_target "$dir/$base"
    [[ "$VALIDATED_TARGET_SNAPSHOT" == "$target_snapshot" ]] || \
      fail "existing environment target changed during creation"
    mv -f -- "$temp" "$dir/$base"
  else
    revalidate_parent "$requested_dir" "$dir" "$parent_snapshot"
    # A same-directory hard link makes the fully written file visible atomically
    # and fails closed if another process wins the target-name race.
    ln -- "$temp" "$dir/$base" || fail "environment target already exists"
  fi
  rm -f -- "$temp"
  trap - EXIT
}

validate_safe_parent() {
  local requested=$1 physical lexical snapshot mode owner permissions uid
  [[ -d "$requested" ]] || fail "environment parent must be an existing real directory"
  physical=$(realpath -e -- "$requested") || \
    fail "environment parent must be an existing real directory"
  lexical=$(realpath -e -s -- "$requested") || \
    fail "environment parent must be an existing real directory"
  [[ "$physical" == "$lexical" ]] || \
    fail "environment parent path must not contain symlinks"
  snapshot=$(stat -c '%d:%i:%a:%u' -- "$physical") || \
    fail "could not inspect environment parent"
  IFS=: read -r _ _ mode owner <<< "$snapshot"
  permissions=$((8#$mode))
  uid=$(id -u)
  if [[ "$owner" == 0 ]] && (( (permissions & 01000) != 0 )); then
    :
  elif [[ "$owner" == "$uid" ]]; then
    (( (permissions & 0022) == 0 )) || \
      fail "environment parent must not be writable by group or others"
  else
    fail "environment parent must be caller-owned or a root-owned sticky directory"
  fi
  VALIDATED_PARENT_PATH=$physical
  VALIDATED_PARENT_SNAPSHOT=$snapshot
}

revalidate_parent() {
  local requested=$1 expected_path=$2 expected_snapshot=$3
  local physical lexical snapshot
  physical=$(realpath -e -- "$requested") || fail "environment parent changed during creation"
  lexical=$(realpath -e -s -- "$requested") || fail "environment parent changed during creation"
  [[ "$physical" == "$expected_path" && "$lexical" == "$expected_path" ]] || \
    fail "environment parent changed during creation"
  snapshot=$(stat -c '%d:%i:%a:%u' -- "$physical") || \
    fail "environment parent changed during creation"
  [[ "$snapshot" == "$expected_snapshot" ]] || \
    fail "environment parent changed during creation"
}

validate_empty_target() {
  local target=$1 snapshot device inode mode owner links size type
  [[ ! -L "$target" ]] || fail "environment target must be a regular non-symlink file"
  snapshot=$(stat -c '%d:%i:%a:%u:%h:%s:%F' -- "$target") || \
    fail "environment target must be a regular non-symlink file"
  IFS=: read -r device inode mode owner links size type <<< "$snapshot"
  [[ "$type" == "regular file" || "$type" == "regular empty file" ]] || \
    fail "environment target must be a regular non-symlink file"
  [[ "$mode" == 600 ]] || fail "existing environment target must have mode 0600"
  [[ "$owner" == "$(id -u)" ]] || fail "existing environment target must be owned by the caller"
  [[ "$links" == 1 ]] || fail "existing environment target must not have hard links"
  [[ "$size" == 0 ]] || fail "existing environment target must be empty"
  VALIDATED_TARGET_SNAPSHOT=$snapshot
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
  [[ "$M1_TEST_ENDPOINTS_READY" =~ ^[01]$ ]] || fail "unsafe endpoint readiness"
  [[ "$M1_TEST_POSTGRES_PORT" =~ ^(0|[1-9][0-9]{3,4})$ ]] || fail "unsafe PostgreSQL port"
  [[ "$M1_TEST_KEYCLOAK_PORT" =~ ^(0|[1-9][0-9]{3,4})$ ]] || fail "unsafe Keycloak port"
  [[ "$M1_TEST_MINIO_PORT" =~ ^(0|[1-9][0-9]{3,4})$ ]] || fail "unsafe MinIO port"
  [[ "$M1_TEST_API_CALLBACK_PORT" =~ ^(0|[1-9][0-9]{3,4})$ ]] || fail "unsafe API callback port"
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
  local -a requested=("$@") initial=()
  (( ${#requested[@]} > 0 )) || requested=("${SERVICES[@]}")
  local service want_postgres=false want_keycloak=false want_minio=false
  local want_bootstrap=false want_callback=false
  for service in "${requested[@]}"; do
    case "$service" in
      postgres) want_postgres=true ;;
      keycloak) want_keycloak=true; want_callback=true ;;
      minio) want_minio=true ;;
      minio-bootstrap) want_bootstrap=true; want_minio=true ;;
      callback-reservation) want_callback=true ;;
    esac
  done
  [[ "$want_postgres" == false ]] || initial+=(postgres)
  [[ "$want_minio" == false ]] || initial+=(minio)
  [[ "$want_callback" == false ]] || initial+=(callback-reservation)
  (( ${#initial[@]} == 0 )) || compose "$env_file" up --detach "${initial[@]}"

  [[ "$want_postgres" == false ]] || resolve_port "$env_file" postgres 5432 \
    M1_TEST_POSTGRES_PORT
  [[ "$want_minio" == false ]] || resolve_port "$env_file" minio 9000 \
    M1_TEST_MINIO_PORT
  [[ "$want_callback" == false ]] || resolve_port "$env_file" callback-reservation 8080 \
    M1_TEST_API_CALLBACK_PORT
  refresh_endpoints
  persist_env "$env_file"

  if [[ "$want_keycloak" == true ]]; then
    compose "$env_file" up --detach keycloak
    resolve_port "$env_file" keycloak 8080 M1_TEST_KEYCLOAK_PORT
    refresh_endpoints
    persist_env "$env_file"
  fi
  [[ "$want_bootstrap" == false ]] || compose "$env_file" up --detach minio-bootstrap
  M1_TEST_ENDPOINTS_READY=1
  export M1_TEST_ENDPOINTS_READY
  persist_env "$env_file"
}

resolve_port() {
  local env_file=$1 service=$2 container_port=$3 variable=$4 published port
  published=$(compose "$env_file" port "$service" "$container_port") || \
    fail "could not resolve Compose-assigned provider port"
  [[ "$published" =~ ^127\.0\.0\.1:([1-9][0-9]{3,4})$ ]] || \
    fail "unsafe Compose-assigned provider port"
  port=${BASH_REMATCH[1]}
  (( port <= 65535 )) || fail "unsafe Compose-assigned provider port"
  printf -v "$variable" '%s' "$port"
  export "$variable"
}

wait_env() {
  local env_file=$1
  shift
  load_env "$env_file"
  require_ready
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
        minio-bootstrap:"exited  0"|callback-reservation:"running  0"|*:"running healthy 0") ;;
        *:"exited "*|*:"dead "*) fail "M1 test provider failed before readiness" ;;
        *) all_ready=false ;;
      esac
    done
    [[ "$all_ready" == true ]] && return 0
    sleep 1
  done
  fail "timed out waiting for M1 test providers"
}

require_ready() {
  [[ "$M1_TEST_ENDPOINTS_READY" == 1 ]] || \
    fail "M1 test provider endpoints are not ready; run up and re-source the environment"
}

require_ready_env() {
  load_env "$1"
  require_ready
}

down_env() {
  local env_file=$1 residual
  [[ -e "$env_file" || -L "$env_file" ]] || return 0
  load_env "$env_file"
  compose "$env_file" down --volumes --remove-orphans || return $?
  residual=$(docker ps -aq --filter \
    "label=com.docker.compose.project=$M1_TEST_PROJECT_NAME") || \
    fail "could not verify M1 test container cleanup"
  [[ -z "$residual" ]] || fail "M1 test project containers remain after cleanup"
  residual=$(docker volume ls -q --filter \
    "label=com.docker.compose.project=$M1_TEST_PROJECT_NAME") || \
    fail "could not verify M1 test volume cleanup"
  [[ -z "$residual" ]] || fail "M1 test project volumes remain after cleanup"
  residual=$(docker network ls -q --filter \
    "label=com.docker.compose.project=$M1_TEST_PROJECT_NAME") || \
    fail "could not verify M1 test network cleanup"
  [[ -z "$residual" ]] || fail "M1 test project networks remain after cleanup"
  rm -f -- "$env_file"
}

usage() {
  printf 'usage: %s {create|up|require-ready|wait|down} <environment-file> [services...]\n' \
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
  require-ready) (( $# == 0 )) || usage; require_ready_env "$env_file" ;;
  wait) wait_env "$env_file" "$@" ;;
  down) (( $# == 0 )) || usage; down_env "$env_file" ;;
  *) usage ;;
esac
