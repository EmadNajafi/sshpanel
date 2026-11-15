#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
if ! systemctl is-active --quiet sshvpn-egress || ! /usr/sbin/nft list table inet sshvpn_egress >/dev/null; then
  echo 'VPN egress isolation must be active before enabling shared-port logins.' >&2
  exit 1
fi

main=/etc/ssh/sshd_config
deny=/etc/ssh/sshd_config.d/05-sshvpn-deny.conf
marker='# sshvpn-panel shared-port policy; keep this block at the end of sshd_config'
backup_dir="${1:-/var/backups/sshvpn-panel/shared-ssh-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
install -d -m 0700 -o root -g root "$backup_dir"
cp -a "$main" "$backup_dir/sshd_config"
had_deny=0
if [[ -e "$deny" ]]; then
  if [[ "$(cat "$deny")" != 'DenyGroups sshvpn' ]]; then
    echo 'Unexpected VPN DenyGroups configuration; refusing to replace it.' >&2
    exit 1
  fi
  cp -a "$deny" "$backup_dir/05-sshvpn-deny.conf"
  had_deny=1
fi

restore=1
rollback() {
  if [[ $restore -eq 1 ]]; then
    cp -a "$backup_dir/sshd_config" "$main"
    if [[ $had_deny -eq 1 ]]; then
      cp -a "$backup_dir/05-sshvpn-deny.conf" "$deny"
    else
      rm -f "$deny"
    fi
    systemctl reload ssh >/dev/null 2>&1 || true
    echo "SSH configuration restored from $backup_dir" >&2
  fi
}
trap rollback EXIT

if ! grep -Fqx "$marker" "$main"; then
  cat >> "$main" <<EOF

$marker
Match Group sshvpn
    PasswordAuthentication yes
    PubkeyAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
    MaxSessions 0
    ForceCommand /usr/sbin/nologin
EOF
fi
# Keep 05-sshvpn-deny.conf when present: it closes the main port to VPN users
# once they use the dedicated VPN SSH port. The Match block above stays as a
# second line of defence.
/usr/sbin/sshd -t

group_id="$(getent group sshvpn | cut -d: -f3)"
account="$(getent passwd | awk -F: -v gid="$group_id" '$4 == gid {print $1; exit}')"
if [[ -n "$account" ]]; then
  policy="$(/usr/sbin/sshd -T -C "user=$account,host=localhost,addr=127.0.0.1")"
  for expected in 'maxsessions 0' 'allowtcpforwarding local' 'permittty no' \
                  'forcecommand /usr/sbin/nologin' 'passwordauthentication yes' \
                  'pubkeyauthentication no' 'allowstreamlocalforwarding no'; do
    if ! grep -Fxq "$expected" <<< "$policy"; then
      echo "VPN SSH restriction is ineffective: $expected" >&2
      exit 1
    fi
  done
fi
# A socket-activated, idle ssh.service reads the file when it next starts.
if systemctl is-active --quiet ssh.service; then
  systemctl reload ssh.service
fi
restore=0
trap - EXIT
if [[ -e "$deny" ]]; then
  echo "VPN logins are closed on the main SSH port; VPN accounts use the dedicated VPN port."
else
  echo "VPN logins are still allowed on the main SSH port during migration; close it in Settings > SSH access."
fi
echo "SSH configuration backup: $backup_dir"
