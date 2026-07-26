#!/bin/sh
set -eu

umask 077

app_dir=${PEERASSIST_APP_DIR:-/root/PeerAssist}
env_file=${PEERASSIST_PLATFORM_ENV_FILE:-/etc/peerassist/platform.env}
project=${PEERASSIST_PLATFORM_PROJECT:-peerassist-platform}
base_compose=$app_dir/infrastructure/compose/compose.m1.yml
platform_compose=$app_dir/infrastructure/compose/compose.platform.yml

[ -d "$app_dir/.git" ] || { echo "PeerAssist repository is unavailable" >&2; exit 2; }
[ -f "$env_file" ] || { echo "PeerAssist platform environment is unavailable" >&2; exit 2; }
[ "$(stat -c '%a' "$env_file")" = 600 ] || {
  echo "PeerAssist platform environment must use mode 0600" >&2
  exit 2
}

compose() {
  docker compose --project-name "$project" --env-file "$env_file" \
    -f "$base_compose" -f "$platform_compose" "$@"
}

case ${1-} in
  start)
    compose up --build --detach --remove-orphans
    deadline=$(( $(date +%s) + 420 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
      if curl --fail --silent --max-time 3 \
          http://127.0.0.1:8000/api/v1/ready >/dev/null 2>&1 \
        && [ -n "$(compose ps --status running --quiet worker)" ]; then
        exit 0
      fi
      sleep 2
    done
    compose ps >&2
    echo "PeerAssist platform did not become ready" >&2
    exit 1
    ;;
  stop)
    compose stop
    ;;
  *)
    echo "usage: ${0##*/} {start|stop}" >&2
    exit 2
    ;;
esac
