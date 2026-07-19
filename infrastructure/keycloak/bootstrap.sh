#!/usr/bin/env bash
set -euo pipefail

require_safe() {
  local name=$1 value=${!1-}
  if [[ ! "$value" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    printf 'invalid generated Keycloak input: %s\n' "$name" >&2
    exit 2
  fi
}

for name in \
  M1_TEST_OIDC_REALM \
  M1_TEST_OIDC_AUTOMATION_CLIENT_ID \
  M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET \
  M1_TEST_OIDC_PKCE_CLIENT_ID \
  M1_TEST_OIDC_USER_PASSWORD
do
  require_safe "$name"
done

if [[ ! "${M1_TEST_PUBLIC_ORIGIN-}" =~ ^http://127\.0\.0\.1:[0-9]{4,5}$ ]]; then
  printf 'invalid generated Keycloak input: M1_TEST_PUBLIC_ORIGIN\n' >&2
  exit 2
fi
if [[ "${M1_TEST_OIDC_REDIRECT_URI-}" != \
  "$M1_TEST_PUBLIC_ORIGIN/api/v1/auth/callback" ]]; then
  printf 'invalid generated Keycloak input: M1_TEST_OIDC_REDIRECT_URI\n' >&2
  exit 2
fi
identity_path=${M1_TEST_IDENTITY_PATH_PREFIX-}
if [[ -n "$identity_path" && "$identity_path" != "/identity" ]]; then
  printf 'invalid generated Keycloak input: M1_TEST_IDENTITY_PATH_PREFIX\n' >&2
  exit 2
fi

umask 077
install -d -m 0700 /opt/keycloak/data/import
realm=$(</bootstrap/realm-template.json)
for name in \
  M1_TEST_OIDC_REALM \
  M1_TEST_OIDC_AUTOMATION_CLIENT_ID \
  M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET \
  M1_TEST_OIDC_PKCE_CLIENT_ID \
  M1_TEST_OIDC_USER_PASSWORD \
  M1_TEST_PUBLIC_ORIGIN \
  M1_TEST_OIDC_REDIRECT_URI
do
  placeholder="\${${name}}"
  realm=${realm//"$placeholder"/${!name}}
done
printf '%s\n' "$realm" > /opt/keycloak/data/import/peerassist-m1-realm.json
unset realm

args=(start --optimized --import-realm --http-enabled=true --http-host=0.0.0.0)
if [[ -n "$identity_path" ]]; then
  args+=(
    --hostname="$M1_TEST_PUBLIC_ORIGIN$identity_path"
    --hostname-backchannel-dynamic=true
  )
fi
exec /opt/keycloak/bin/kc.sh "${args[@]}"
