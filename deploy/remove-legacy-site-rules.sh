#!/usr/bin/env bash
# Upgrade-only cleanup for the retired website allowlist.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config=/etc/ssh/sshd_config
candidate="$(mktemp /etc/ssh/.sshvpn-cleanup.XXXXXX)"
trap 'rm -f -- "$candidate"' EXIT
PYTHONPATH="$repo_dir/helper" python3 - "$config" > "$candidate" <<'PY'
from pathlib import Path
import sys
from sshvpn_legacy_site_cleanup import without_website_rules

print(without_website_rules(Path(sys.argv[1]).read_text(encoding="utf-8")), end="")
PY
chmod --reference="$config" "$candidate"
chown --reference="$config" "$candidate"

if ! cmp -s "$config" "$candidate"; then
  /usr/sbin/sshd -t -f "$candidate"
  original_root="$(/usr/sbin/sshd -T -f "$config" -C user=root,host=localhost,addr=127.0.0.1)"
  updated_root="$(/usr/sbin/sshd -T -f "$candidate" -C user=root,host=localhost,addr=127.0.0.1)"
  if [[ "$original_root" != "$updated_root" ]]; then
    echo 'Cleanup would change administrator SSH; refusing to continue.' >&2
    exit 1
  fi
  install -d -m 0700 /var/backups/sshvpn-panel
  backup="/var/backups/sshvpn-panel/before-site-rule-removal-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  cp -a "$config" "$backup"
  mv -f "$candidate" "$config"
  if ! systemctl reload ssh; then
    cp -a "$backup" "$config"
    systemctl reload ssh || true
    echo 'SSH reload failed; previous configuration restored.' >&2
    exit 1
  fi
  # Existing SSH sessions retain their login-time forwarding policy.
  /usr/local/sbin/sshvpnctl disconnect-all
fi

rm -f -- /etc/sshvpn/site-access.json /run/lock/sshvpn-site-access.lock /usr/local/sbin/sshvpn_sites.py \
  /usr/local/sbin/__pycache__/sshvpn_sites.*.pyc
