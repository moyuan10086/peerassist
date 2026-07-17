#!/bin/sh
set -eu

safe_value() {
  case "$2" in
    ''|*[!A-Za-z0-9_.-]*)
      printf 'invalid generated MinIO input: %s\n' "$1" >&2
      exit 2
      ;;
  esac
}

safe_value M1_TEST_S3_ACCESS_KEY "${M1_TEST_S3_ACCESS_KEY:-}"
safe_value M1_TEST_S3_SECRET_KEY "${M1_TEST_S3_SECRET_KEY:-}"
safe_value M1_TEST_S3_BUCKET "${M1_TEST_S3_BUCKET:-}"

mc alias set peerassist http://minio:9000 \
  "$M1_TEST_S3_ACCESS_KEY" "$M1_TEST_S3_SECRET_KEY" >/dev/null
mc mb --ignore-existing "peerassist/$M1_TEST_S3_BUCKET" >/dev/null
mc anonymous set none "peerassist/$M1_TEST_S3_BUCKET" >/dev/null
mc ilm rule add --expire-days 1 --prefix tmp/ \
  "peerassist/$M1_TEST_S3_BUCKET" >/dev/null
