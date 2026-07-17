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

install -d -m 0700 /opt/keycloak/data/import
realm=$(</bootstrap/realm-template.json)
for name in \
  M1_TEST_OIDC_REALM \
  M1_TEST_OIDC_AUTOMATION_CLIENT_ID \
  M1_TEST_OIDC_AUTOMATION_CLIENT_SECRET \
  M1_TEST_OIDC_PKCE_CLIENT_ID \
  M1_TEST_OIDC_USER_PASSWORD
do
  placeholder="\${${name}}"
  realm=${realm//"$placeholder"/${!name}}
done
printf '%s\n' "$realm" > /opt/keycloak/data/import/peerassist-m1-realm.json
unset realm

exec /opt/keycloak/bin/kc.sh start-dev --import-realm --http-host=0.0.0.0
