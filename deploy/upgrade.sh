#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run as root." >&2; exit 1; fi
if (( $# != 0 )); then echo "Usage: sudo bash deploy/upgrade.sh" >&2; exit 1; fi
if ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -q '^VERSION_ID="24.04"$' /etc/os-release; then
  echo "This upgrade targets Ubuntu 24.04 only." >&2; exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$repo_dir" == /opt/ssh-vpn-panel ]]; then
  echo "Run this upgrade from a separate Git checkout, not the live installation." >&2; exit 1
fi
if [[ ! -f /opt/ssh-vpn-panel/manage.py || ! -x /opt/ssh-vpn-panel/.venv/bin/python || \
      ! -f /etc/sshvpn/panel.env || ! -f /etc/nginx/sites-available/sshvpn-panel ]]; then
  echo "A complete existing SSH VPN panel installation was not found; refusing to change it." >&2; exit 1
fi
/usr/sbin/sshd -t

# Preserve the live configuration, application code, and database before copying files.
set -a
source /etc/sshvpn/panel.env
set +a
backup_dir="/var/backups/sshvpn-panel/upgrade-$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 -o root -g root "$backup_dir"
cp -a /etc/sshvpn/panel.env "$backup_dir/panel.env"
cp -a /etc/nginx/sites-available/sshvpn-panel "$backup_dir/nginx-panel"
if [[ -f /etc/ssh/sshvpn_sshd_config ]]; then
  cp -a /etc/ssh/sshvpn_sshd_config "$backup_dir/sshvpn_sshd_config"
fi
cp -a /etc/pam.d/sshd "$backup_dir/pam-sshd"
cp -a /etc/systemd/system/sshvpn-panel.service "$backup_dir/sshvpn-panel.service"
tar -C /opt --exclude='ssh-vpn-panel/.venv' --exclude='ssh-vpn-panel/staticfiles' \
  -czf "$backup_dir/application.tar.gz" ssh-vpn-panel
runuser -u postgres -- pg_dump -Fc "${DB_NAME:-sshvpn}" > "$backup_dir/database.dump"
chmod 0600 "$backup_dir/database.dump"

# Keep the existing administrator, VPN accounts, listener addresses, and TLS mode.
rsync -a --exclude='.git' --exclude='.venv' --exclude='.env' \
  --exclude='staticfiles' --exclude='__pycache__' "$repo_dir/" /opt/ssh-vpn-panel/
chown -R root:root /opt/ssh-vpn-panel
/opt/ssh-vpn-panel/.venv/bin/pip install --no-cache-dir -r /opt/ssh-vpn-panel/requirements.txt

if ! grep -q '^PANEL_HTTP_PORT=' /etc/sshvpn/panel.env; then
  if grep -Eq '^[[:space:]]*listen[[:space:]]+127\.0\.0\.1:8080;' /etc/nginx/sites-available/sshvpn-panel; then
    printf '%s\n' 'PANEL_HTTP_PORT=8080' >> /etc/sshvpn/panel.env
  else
    printf '%s\n' 'PANEL_HTTP_PORT=80' >> /etc/sshvpn/panel.env
  fi
fi
main_ssh_port="$(/usr/sbin/sshd -T | awk '$1 == "port" {print $2; exit}')"
if grep -q '^VPN_SSH_PORT=' /etc/sshvpn/panel.env; then
  sed -i -E "s/^VPN_SSH_PORT=.*/VPN_SSH_PORT=$main_ssh_port/" /etc/sshvpn/panel.env
else
  printf 'VPN_SSH_PORT=%s\n' "$main_ssh_port" >> /etc/sshvpn/panel.env
fi
chown root:sshvpn-panel /etc/sshvpn/panel.env
chmod 0640 /etc/sshvpn/panel.env
set -a
source /etc/sshvpn/panel.env
set +a

install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpnctl /usr/local/sbin/sshvpnctl
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_policy.py /usr/local/sbin/sshvpn_policy.py
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn-authz /usr/local/sbin/sshvpn-authz
install -d -m 0700 -o root -g root /etc/sshvpn/accounts
install -m 0755 -o root -g root "$repo_dir/deploy/install-pam-hook.sh" /usr/local/sbin/sshvpn-install-pam-hook
install -m 0755 -o root -g root "$repo_dir/deploy/configure-main-ssh.sh" /usr/local/sbin/sshvpn-configure-main-ssh
install -m 0644 "$repo_dir/deploy/sshvpn-egress.service" /etc/systemd/system/sshvpn-egress.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.service" /etc/systemd/system/sshvpn-policy.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.timer" /etc/systemd/system/sshvpn-policy.timer
install -m 0644 "$repo_dir/deploy/sshvpn-panel.service" /etc/systemd/system/sshvpn-panel.service
install -m 0755 -o root -g root /opt/ssh-vpn-panel/deploy/menu.sh /usr/local/bin/sshvpn-menu
nginx -t
/usr/sbin/sshd -t
cd /opt/ssh-vpn-panel
runuser -u sshvpn-panel -- .venv/bin/python manage.py check
runuser -u sshvpn-panel -- .venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
# Older VPN accounts retain their previous unlimited policy on upgrade.
/usr/local/sbin/sshvpnctl seed-legacy
sshvpn-install-pam-hook "$backup_dir/pam-sshd"
systemctl daemon-reload
systemctl enable --now sshvpn-egress
sshvpn-configure-main-ssh "$backup_dir"
if systemctl cat sshvpn-sshd.service >/dev/null 2>&1; then
  systemctl disable --now sshvpn-sshd.service
fi
systemctl enable --now sshvpn-policy.timer
systemctl restart sshvpn-panel
systemctl is-active --quiet sshvpn-panel

echo "Upgrade complete. Existing accounts and web administrator were retained."
echo "Backup: $backup_dir"
echo "Panel URL and SSL mode remain as configured before this upgrade."
