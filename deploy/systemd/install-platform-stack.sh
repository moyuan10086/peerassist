#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root to install PeerAssist platform systemd files" >&2
  exit 2
fi

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)
unit_dir=/etc/systemd/system
ui_dropin_dir=$unit_dir/peerassist-ui.service.d

install -m 0755 \
  "$repo_root/deploy/systemd/peerassist-platform-stack.sh" \
  /usr/local/sbin/peerassist-platform-stack
install -m 0755 \
  "$repo_root/deploy/systemd/peerassist-ui-start.sh" \
  /usr/local/sbin/peerassist-ui-start
install -m 0644 \
  "$repo_root/deploy/systemd/peerassist-platform-stack.service" \
  "$unit_dir/peerassist-platform-stack.service"
install -d -m 0755 "$ui_dropin_dir"
install -m 0644 \
  "$repo_root/deploy/systemd/peerassist-ui-platform.conf" \
  "$ui_dropin_dir/platform.conf"

systemctl daemon-reload
systemctl enable peerassist-platform-stack.service
