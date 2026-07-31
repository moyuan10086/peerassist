#!/bin/sh
set -eu

umask 077

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root to verify a PeerAssist backup" >&2
  exit 2
fi
if [ "$#" -ne 1 ]; then
  echo "usage: ${0##*/} BACKUP_BUNDLE" >&2
  exit 2
fi

bundle=$(CDPATH= cd -- "$1" && pwd -P)
[ -f "$bundle/SHA256SUMS" ] || { echo "SHA256SUMS is unavailable" >&2; exit 2; }
[ -f "$bundle/postgres.dump" ] || { echo "postgres.dump is unavailable" >&2; exit 2; }

(
  cd "$bundle"
  sha256sum -c SHA256SUMS
)
for archive in keycloak-data.tgz minio-data.tgz model-settings-data.tgz worker-scratch.tgz; do
  tar -tzf "$bundle/$archive" >/dev/null
done

container=peerassist-restore-drill-$$
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

docker run --detach --rm --name "$container" --network none   --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=512m   -v "$bundle:/backup:ro"   -e POSTGRES_USER=restore -e POSTGRES_HOST_AUTH_METHOD=trust   -e POSTGRES_DB=peerassist postgres:16.9-bookworm >/dev/null

ready_deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$ready_deadline" ]; do
  if docker exec "$container" pg_isready -U restore -d peerassist >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$container" pg_isready -U restore -d peerassist >/dev/null

docker exec "$container" pg_restore -U restore -d peerassist   --no-owner --no-privileges /backup/postgres.dump

table_count=$(docker exec "$container" psql -U restore -d peerassist -Atc   'SELECT count(*) FROM information_schema.tables WHERE table_schema=current_schema()')
case $table_count in
  ''|*[!0-9]*|0) echo "restored database has no public tables" >&2; exit 1 ;;
esac

printf 'restored_tables=%s\n' "$table_count"
docker exec "$container" psql -U restore -d peerassist -A -t -F= -c   'SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname'
trap - EXIT HUP INT TERM
docker rm -f "$container" >/dev/null
printf '%s\n' ISOLATED_RESTORE_OK
