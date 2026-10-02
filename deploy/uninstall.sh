#!/usr/bin/env bash
# Remove only resources owned by SSH VPN Panel. Run from an interactive root shell.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root (sudo menu uninstall).' >&2; exit 1; fi
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --dry-run ) ]]; then
  echo 'Usage: sudo menu uninstall [--dry-run]' >&2
  exit 1
fi
if [[ ! -f /etc/sshvpn/panel.env || ! -d /opt/ssh-vpn-panel ]]; then
  echo 'A complete SSH VPN Panel installation was not found; no changes made.' >&2
  exit 1
fi
if [[ -e /etc/sshvpn/ssh-port-pending.json ]]; then
  echo 'Confirm or cancel the pending SSH port change in panel settings first.' >&2
  exit 1
fi
if [[ -e /etc/sshvpn/web-change-pending/state.json ]]; then
  echo 'Confirm or cancel the pending web address change in panel settings first.' >&2
  exit 1
fi

ssh_config=/etc/ssh/sshd_config
pam_config=/etc/pam.d/sshd
marker='# sshvpn-panel shared-port policy; keep this block at the end of sshd_config'
hook='account requisite pam_exec.so quiet /usr/local/sbin/sshvpn-authz'
site=/etc/nginx/sites-available/sshvpn-panel
enabled=/etc/nginx/sites-enabled/sshvpn-panel
checkout=/root/sshpanel

if [[ ! -f $ssh_config || ! -f $pam_config ]]; then
  echo 'SSH or PAM configuration is missing; no changes made.' >&2
  exit 1
fi
if [[ -L $enabled && $(readlink "$enabled") != "$site" ]]; then
  echo 'The Nginx site link points elsewhere; no changes made.' >&2
  exit 1
fi
if [[ -L $checkout ]]; then
  echo "$checkout is a symbolic link; no changes made." >&2
  exit 1
fi
if [[ -e $checkout ]]; then
  if [[ ! -d $checkout/.git ]]; then
    echo "$checkout is not the panel checkout; no changes made." >&2
    exit 1
  fi
  case "$(git -C "$checkout" remote get-url origin 2>/dev/null || true)" in
    https://github.com/EmadNajafi/sshpanel.git|git@github.com:EmadNajafi/sshpanel.git|ssh://git@ssh.github.com:443/EmadNajafi/sshpanel.git) ;;
    *) echo "$checkout points to another repository; no changes made." >&2; exit 1 ;;
  esac
fi

temp_dir=$(mktemp -d)
trap 'rm -rf -- "$temp_dir"' EXIT
python3 - "$ssh_config" "$temp_dir/sshd_config" "$marker" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text()
marker = sys.argv[3]
block = """# sshvpn-panel shared-port policy; keep this block at the end of sshd_config
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
"""
if source.count(marker) != 1 or source[source.index(marker):].rstrip() != block.rstrip():
    raise SystemExit("Managed SSH block has changed; review sshd_config before uninstalling.")
Path(sys.argv[2]).write_text(source[:source.index(marker)].rstrip() + "\n")
PY
/usr/sbin/sshd -t -f "$temp_dir/sshd_config"
if [[ $(grep -Fxc "$hook" "$pam_config" || true) != 1 ]] || \
   [[ $(grep -Fc '/usr/local/sbin/sshvpn-authz' "$pam_config" || true) != 1 ]]; then
  echo 'The SSH PAM hook has changed; review it before uninstalling.' >&2
  exit 1
fi
awk -v hook="$hook" '$0 != hook { print }' "$pam_config" > "$temp_dir/pam-sshd"
if grep -RFl 'sshvpn_login' /etc/nginx/sites-available /etc/nginx/conf.d 2>/dev/null | grep -Fvx -e "$site" -e /etc/nginx/conf.d/sshvpn-rate.conf | grep -q .; then
  echo 'Another Nginx site uses the panel rate limit zone; no changes made.' >&2
  exit 1
fi

python3 - > "$temp_dir/vpn-users" <<'PY'
import grp
import pwd

gid = grp.getgrnam('sshvpn').gr_gid
for user in pwd.getpwall():
    if user.pw_gid == gid:
        if user.pw_dir != '/var/empty' or user.pw_shell != '/usr/sbin/nologin':
            raise SystemExit(f'Unrecognized account in sshvpn group: {user.pw_name}')
        print(user.pw_name)
PY
mapfile -t vpn_users < "$temp_dir/vpn-users"
echo 'This will permanently delete the web panel, PostgreSQL panel database, VPN accounts,'
echo 'stored passwords and secrets, all panel backups, traffic records, services, firewall'
echo 'tables, Nginx site, panel certificate when dedicated to this site, and Git checkout.'
echo "VPN accounts to delete: ${#vpn_users[@]}"
echo 'The current administrator SSH port and PostgreSQL server remain.'
echo 'Nginx is purged if no other site or custom configuration uses it.'
if [[ ${1:-} == --dry-run ]]; then
  echo 'Dry run only; nothing was deleted.'
  exit 0
fi
if [[ ! -t 0 ]]; then
  echo 'Uninstall requires an interactive terminal.' >&2
  exit 1
fi
read -r -p 'Type DELETE SSHVPN to continue: ' answer
if [[ $answer != 'DELETE SSHVPN' ]]; then
  echo 'Cancelled; nothing was deleted.'
  exit 1
fi

# Stop account creation, then lock and remove VPN users before removing their SSH rules.
systemctl disable --now sshvpn-policy.timer sshvpn-usage.timer sshvpn-panel.service 2>/dev/null || true
systemctl stop sshvpn-policy.service sshvpn-usage.service 2>/dev/null || true
if systemctl is-active --quiet sshvpn-panel.service; then
  echo 'The web panel is still running; no VPN accounts were removed.' >&2
  exit 1
fi
bash /opt/ssh-vpn-panel/deploy/cleanup-vpn-users.sh --yes --keep-group

# Remove the SSH integration. Roll it back if the main SSH service cannot reload.
cp -a "$ssh_config" "$temp_dir/original-sshd_config"
cp -a "$pam_config" "$temp_dir/original-pam-sshd"
install -m 0644 -o root -g root "$temp_dir/sshd_config" "$ssh_config"
install -m 0644 -o root -g root "$temp_dir/pam-sshd" "$pam_config"
if ! /usr/sbin/sshd -t || ! systemctl reload ssh; then
  cp -a "$temp_dir/original-sshd_config" "$ssh_config"
  cp -a "$temp_dir/original-pam-sshd" "$pam_config"
  systemctl reload ssh || true
  echo 'SSH reload failed; configuration restored. Uninstall stopped.' >&2
  exit 1
fi

systemctl disable --now sshvpn-sshd.service 2>/dev/null || true
systemctl disable --now sshvpn-egress.service 2>/dev/null || true
if [[ -x /usr/local/sbin/sshvpnctl ]]; then
  /usr/local/sbin/sshvpnctl disconnect-all || true
fi
/usr/sbin/nft delete table inet sshvpn_usage 2>/dev/null || true
/usr/sbin/nft delete table inet sshvpn_egress 2>/dev/null || true
if [[ -L $enabled ]]; then rm -- "$enabled"; fi
if [[ -f $site ]]; then cp -a "$site" "$temp_dir/nginx-panel"; rm -- "$site"; fi
if [[ -f /etc/nginx/conf.d/sshvpn-rate.conf ]]; then
  cp -a /etc/nginx/conf.d/sshvpn-rate.conf "$temp_dir/nginx-rate"
fi
rm -f -- /etc/nginx/conf.d/sshvpn-rate.conf
if ! nginx -t || ! systemctl reload nginx; then
  if [[ -f $temp_dir/nginx-panel ]]; then cp -a "$temp_dir/nginx-panel" "$site"; fi
  if [[ -f $temp_dir/nginx-rate ]]; then cp -a "$temp_dir/nginx-rate" /etc/nginx/conf.d/sshvpn-rate.conf; fi
  if [[ -f $site && ! -e $enabled ]]; then ln -s "$site" "$enabled"; fi
  systemctl reload nginx || true
  echo 'Nginx check failed; its panel configuration was restored. Uninstall stopped.' >&2
  exit 1
fi
# Certbot may share a certificate with another site; never delete a shared cert.
panel_domain=$(sed -n 's/^PANEL_DOMAIN=//p' /etc/sshvpn/panel.env | head -n 1)
if [[ $panel_domain =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]] && \
   [[ -f /etc/letsencrypt/live/$panel_domain/cert.pem ]] && \
   openssl x509 -in "/etc/letsencrypt/live/$panel_domain/cert.pem" -noout -ext subjectAltName | \
     grep -Eq "^[[:space:]]*DNS:$panel_domain[[:space:]]*$" && \
   ! grep -RFl -- "/etc/letsencrypt/live/$panel_domain/" /etc/nginx /etc/apache2 2>/dev/null | grep -q .; then
  certbot delete --cert-name "$panel_domain" --non-interactive || \
    echo "Certificate $panel_domain could not be removed; remove it manually." >&2
fi

runuser -u postgres -- dropdb --if-exists --force sshvpn
runuser -u postgres -- dropuser --if-exists sshvpn
rm -f -- /etc/systemd/system/sshvpn-egress.service /etc/systemd/system/sshvpn-panel.service \
  /etc/systemd/system/sshvpn-policy.service /etc/systemd/system/sshvpn-policy.timer \
  /etc/systemd/system/sshvpn-usage.service /etc/systemd/system/sshvpn-usage.timer \
  /etc/systemd/system/sshvpn-sshd.service /etc/ssh/sshvpn_sshd_config \
  /etc/sudoers.d/sshvpn-panel
systemctl daemon-reload
systemctl reset-failed sshvpn-egress.service sshvpn-panel.service sshvpn-policy.service sshvpn-usage.service 2>/dev/null || true

if id sshvpn-panel >/dev/null 2>&1; then userdel sshvpn-panel; fi
if getent group sshvpn-panel >/dev/null; then groupdel sshvpn-panel; fi
if getent group sshvpn >/dev/null; then groupdel sshvpn; fi

if ! bash /opt/ssh-vpn-panel/deploy/purge-unused-nginx.sh --yes; then
  echo 'Nginx was kept because it has other configuration or could not be purged.' >&2
fi
rm -rf -- /opt/ssh-vpn-panel /etc/sshvpn /var/lib/sshvpn-panel /var/lib/sshvpn /var/backups/sshvpn-panel
rm -rf -- /run/sshvpn-policy
rm -f -- /usr/local/sbin/sshvpnctl /usr/local/sbin/sshvpn-backup \
  /usr/local/sbin/sshvpn_policy.py /usr/local/sbin/sshvpn_port.py \
  /usr/local/sbin/sshvpn_usage.py /usr/local/sbin/sshvpn_web.py \
  /usr/local/sbin/sshvpn-authz /usr/local/sbin/sshvpn-install-pam-hook \
  /usr/local/sbin/sshvpn-configure-main-ssh
rm -f -- /usr/local/sbin/__pycache__/sshvpn_*.pyc /run/lock/sshvpn-backup.lock
rmdir /usr/local/sbin/__pycache__ 2>/dev/null || true
if [[ -L /usr/local/bin/menu && $(readlink /usr/local/bin/menu) == /usr/local/bin/sshvpn-menu ]]; then
  rm -- /usr/local/bin/menu
fi
rm -f -- /usr/local/bin/sshvpn-menu
if [[ -d $checkout ]]; then rm -rf -- "$checkout"; fi
echo 'SSH VPN Panel was removed. The current administrator SSH port remains.'
