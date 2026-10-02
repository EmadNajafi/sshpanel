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
  --exclude='staticfiles' --exclude='__pycache__' --exclude='frontend/node_modules' "$repo_dir/" /opt/ssh-vpn-panel/
chown -R root:root /opt/ssh-vpn-panel
/opt/ssh-vpn-panel/.venv/bin/pip install --no-cache-dir -r /opt/ssh-vpn-panel/requirements.txt

if ! grep -q '^PANEL_HTTP_PORT=' /etc/sshvpn/panel.env; then
  if grep -Eq '^[[:space:]]*listen[[:space:]]+127\.0\.0\.1:8080;' /etc/nginx/sites-available/sshvpn-panel; then
    printf '%s\n' 'PANEL_HTTP_PORT=8080' >> /etc/sshvpn/panel.env
  else
    printf '%s\n' 'PANEL_HTTP_PORT=80' >> /etc/sshvpn/panel.env
  fi
fi
if ! grep -q '^PANEL_WEB_PATH=' /etc/sshvpn/panel.env; then
  printf '%s\n' 'PANEL_WEB_PATH=' >> /etc/sshvpn/panel.env
fi
if ! grep -q '^PANEL_HTTPS_PORT=' /etc/sshvpn/panel.env; then
  printf '%s\n' 'PANEL_HTTPS_PORT=443' >> /etc/sshvpn/panel.env
fi
main_ssh_port="$(/usr/sbin/sshd -T | awk '$1 == "port" && !found {print $2; found=1}')"
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
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_backup.py /usr/local/sbin/sshvpn-backup
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_user_import.py /usr/local/sbin/sshvpn_user_import.py
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_policy.py /usr/local/sbin/sshvpn_policy.py
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_port.py /usr/local/sbin/sshvpn_port.py
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_usage.py /usr/local/sbin/sshvpn_usage.py
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_web.py /usr/local/sbin/sshvpn_web.py
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn-authz /usr/local/sbin/sshvpn-authz
install -d -m 0700 -o root -g root /etc/sshvpn/accounts
install -d -m 0750 -o root -g sshvpn-panel /var/lib/sshvpn-panel/backups
install -d -m 0700 -o sshvpn-panel -g sshvpn-panel /var/lib/sshvpn-panel/restore-uploads
printf '%s\n' 'sshvpn-panel ALL=(root) NOPASSWD: /usr/local/sbin/sshvpnctl, /usr/local/sbin/sshvpn-backup' > /etc/sudoers.d/sshvpn-panel
chmod 0440 /etc/sudoers.d/sshvpn-panel
visudo -cf /etc/sudoers.d/sshvpn-panel
if ! grep -q 'client_max_body_size' /etc/nginx/sites-available/sshvpn-panel; then
  sed -i '/^[[:space:]]*server[[:space:]]*{/a\    client_max_body_size 1100m;' /etc/nginx/sites-available/sshvpn-panel
fi
if ! grep -q 'proxy_read_timeout 420s' /etc/nginx/sites-available/sshvpn-panel; then
  sed -i '/^[[:space:]]*server[[:space:]]*{/a\    proxy_read_timeout 420s;' /etc/nginx/sites-available/sshvpn-panel
fi
install -m 0755 -o root -g root "$repo_dir/deploy/install-pam-hook.sh" /usr/local/sbin/sshvpn-install-pam-hook
install -m 0755 -o root -g root "$repo_dir/deploy/configure-main-ssh.sh" /usr/local/sbin/sshvpn-configure-main-ssh
install -m 0644 "$repo_dir/deploy/sshvpn-egress.service" /etc/systemd/system/sshvpn-egress.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.service" /etc/systemd/system/sshvpn-policy.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.timer" /etc/systemd/system/sshvpn-policy.timer
install -m 0644 "$repo_dir/deploy/sshvpn-usage.service" /etc/systemd/system/sshvpn-usage.service
install -m 0644 "$repo_dir/deploy/sshvpn-usage.timer" /etc/systemd/system/sshvpn-usage.timer
install -m 0644 "$repo_dir/deploy/sshvpn-panel.service" /etc/systemd/system/sshvpn-panel.service
install -m 0755 -o root -g root /opt/ssh-vpn-panel/deploy/menu.sh /usr/local/bin/sshvpn-menu
# Existing private-path sites need the same neutral response as new sites.
python3 - <<'PY'
from pathlib import Path
import re

site = Path("/etc/nginx/sites-available/sshvpn-panel")
old = site.read_text(encoding="utf-8")
updated = old.replace('location / { return 404; }',
                      'location / { default_type text/plain; return 404 "Not Found"; }')
if "server_tokens off;" not in updated:
    updated = re.sub(r"(?m)^server \{$", "server {\n    server_tokens off;", updated)
if updated != old:
    site.write_text(updated, encoding="utf-8")
PY
nginx -t
systemctl reload nginx
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
/usr/local/sbin/sshvpnctl usage-sync
sshvpn-configure-main-ssh "$backup_dir"
if systemctl cat sshvpn-sshd.service >/dev/null 2>&1; then
  systemctl disable --now sshvpn-sshd.service
fi
systemctl enable --now sshvpn-policy.timer
systemctl enable --now sshvpn-usage.timer
systemctl restart sshvpn-panel
systemctl is-active --quiet sshvpn-panel
if git -C "$repo_dir" rev-parse --verify HEAD >/dev/null 2>&1; then
  git -C "$repo_dir" rev-parse --verify HEAD > /opt/ssh-vpn-panel/.installed-commit
fi

echo "Upgrade complete. Existing accounts and web administrator were retained."
echo "Backup: $backup_dir"
echo "Panel URL and SSL mode remain as configured before this upgrade."
