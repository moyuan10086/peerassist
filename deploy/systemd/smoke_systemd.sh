#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "run as root so the disposable system identity and units can be installed" >&2
  exit 2
fi
if [[ ! -d /run/systemd/system ]] || ! systemctl is-system-running --quiet; then
  state=$(systemctl is-system-running 2>/dev/null || true)
  if [[ ${state} != degraded ]]; then
    echo "a running systemd host is required" >&2
    exit 2
  fi
fi

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
if [[ -n $(git -C "${repo_root}" status --porcelain=v1) ]]; then
  echo "the systemd smoke requires a clean checkout" >&2
  exit 2
fi
unit_dir=/run/systemd/system
review_unit=${unit_dir}/peerassist-review-api.service
workspace_unit=${unit_dir}/peerassist-ui.service
install_root=/opt/peerassist
environment_dir=/etc/peerassist
environment_file=${environment_dir}/peerassist.env
data_root=/var/lib/peerassist

refuse() {
  echo "refusing to replace or reuse existing resource: $1" >&2
  exit 2
}

getent group peerassist >/dev/null && refuse "group peerassist"
for identity in peerassist-api peerassist-ui; do
  getent passwd "${identity}" >/dev/null && refuse "user ${identity}"
  getent group "${identity}" >/dev/null && refuse "group ${identity}"
done
[[ -e ${install_root} || -L ${install_root} ]] && refuse "${install_root}"
[[ -e ${environment_dir} || -L ${environment_dir} ]] && refuse "${environment_dir}"
[[ -e ${data_root} || -L ${data_root} ]] && refuse "${data_root}"
[[ -e ${review_unit} || -L ${review_unit} ]] && refuse "${review_unit}"
[[ -e ${workspace_unit} || -L ${workspace_unit} ]] && refuse "${workspace_unit}"
systemctl cat peerassist-review-api.service >/dev/null 2>&1 && refuse "installed review API unit"
systemctl cat peerassist-ui.service >/dev/null 2>&1 && refuse "installed workspace unit"
systemctl is-active --quiet peerassist-review-api.service && refuse "active review API unit"
systemctl is-active --quiet peerassist-ui.service && refuse "active workspace unit"
ss -H -ltn '( sport = :8766 or sport = :8767 )' | grep -q . && refuse "ports 8766/8767"

created_identities=false
cleanup() {
  set +e
  systemctl stop peerassist-ui.service peerassist-review-api.service >/dev/null 2>&1
  rm -f -- "${workspace_unit}" "${review_unit}"
  systemctl daemon-reload >/dev/null 2>&1
  rm -rf -- "${install_root}"
  rm -rf -- "${environment_dir}" "${data_root}"
  if [[ ${created_identities} == true ]]; then
    userdel peerassist-ui >/dev/null 2>&1
    userdel peerassist-api >/dev/null 2>&1
    groupdel peerassist-ui >/dev/null 2>&1
    groupdel peerassist-api >/dev/null 2>&1
    groupdel peerassist >/dev/null 2>&1
  fi
}
trap cleanup EXIT

groupadd --system peerassist
useradd --system --user-group --groups peerassist --home-dir /nonexistent \
  --shell /usr/sbin/nologin peerassist-api
useradd --system --user-group --groups peerassist --home-dir /nonexistent \
  --shell /usr/sbin/nologin peerassist-ui
created_identities=true
install -d -m 0755 "${install_root}"
git -C "${repo_root}" ls-files -z | tar -C "${repo_root}" --null -T - -cf - | tar -xf - -C "${install_root}"
cp -aL -- "${repo_root}/.venv" "${install_root}/.venv"
chown -R root:peerassist "${install_root}"
chmod -R a+rX "${install_root}"
install -d -m 0750 -o root -g peerassist "${environment_dir}"
install -m 0640 -o root -g peerassist \
  "${repo_root}/deploy/systemd/peerassist.env.example" "${environment_file}"
install -d -m 0750 -o peerassist-api -g peerassist-api "${data_root}/data"
install -d -m 0750 -o peerassist-ui -g peerassist-ui \
  "${data_root}/workspace/data" "${data_root}/workspace/run/stages/peerassist"
install -m 0640 -o peerassist-ui -g peerassist-ui \
  "${repo_root}/tests/fixtures/contracts/public_test.pdf" \
  "${data_root}/workspace/run/paper.pdf"
cp -- "${repo_root}/deploy/systemd/peerassist-review-api.service" "${review_unit}"
cp -- "${repo_root}/deploy/systemd/peerassist-ui.service" "${workspace_unit}"
cmp -- "${install_root}/deploy/systemd/peerassist-review-api.service" "${review_unit}"
cmp -- "${install_root}/deploy/systemd/peerassist-ui.service" "${workspace_unit}"
cmp -- "${repo_root}/deploy/systemd/peerassist-review-api.service" "${review_unit}"
cmp -- "${repo_root}/deploy/systemd/peerassist-ui.service" "${workspace_unit}"
systemd-analyze verify "${review_unit}" "${workspace_unit}"

systemctl daemon-reload
systemctl start peerassist-review-api.service peerassist-ui.service
systemctl is-active --quiet peerassist-review-api.service
systemctl is-active --quiet peerassist-ui.service

for _ in {1..100}; do
  curl --fail --silent http://127.0.0.1:8767/api/health >/dev/null 2>&1 && break
  sleep 0.05
done
curl --fail --silent http://127.0.0.1:8767/api/health >/dev/null
for _ in {1..100}; do
  curl --fail --silent http://127.0.0.1:8766/ >/dev/null 2>&1 && break
  sleep 0.05
done
curl --fail --silent http://127.0.0.1:8766/ >/dev/null
curl --fail --silent http://127.0.0.1:8766/api/health >/dev/null
review_pid=$(systemctl show --property MainPID --value peerassist-review-api.service)
workspace_pid=$(systemctl show --property MainPID --value peerassist-ui.service)
! runuser -u peerassist-ui -- test -r "/proc/${review_pid}/root/var/lib/peerassist/data"
! runuser -u peerassist-api -- test -r "/proc/${workspace_pid}/root/var/lib/peerassist/workspace"
