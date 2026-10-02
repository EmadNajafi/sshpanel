#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run as root." >&2; exit 1; fi
if [[ ! -t 0 ]]; then echo "Run the installer in an interactive terminal to set administrator credentials." >&2; exit 1; fi
if (( $# > 2 )) || { (( $# == 2 )) && [[ "$1" == "--local-test" ]]; }; then
  echo "Usage: sudo bash deploy/install.sh [PANEL_HOST [EMAIL]] | --local-test" >&2
  exit 1
fi
if ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -q '^VERSION_ID="24.04"$' /etc/os-release; then
  echo "This installer targets Ubuntu 24.04 only." >&2; exit 1
fi
if ! grep -Eq '^Include /etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config; then
  echo "Main sshd does not include sshd_config.d; refusing to install." >&2; exit 1
fi
if [[ -e /opt/ssh-vpn-panel || -e /etc/sshvpn/panel.env ]]; then
  echo "Existing installation found. To preserve its database and accounts, run: sudo bash deploy/upgrade.sh" >&2; exit 1
fi

printf '\033[H\033[2J'
echo 'SSH VPN Panel installation'
echo
echo 'Default web administrator: admin / 123456'
while :; do
  read -r -p 'Use these credentials? [Y/n]: ' use_default_admin
  case "${use_default_admin,,}" in
    ''|y|yes) admin_username=admin; admin_password=123456; admin_confirmation=123456; break ;;
    n|no)
      read -r -p 'Administrator username: ' admin_username
      read -r -s -p 'Administrator password: ' admin_password
      echo
      read -r -s -p 'Confirm administrator password: ' admin_confirmation
      echo
      break ;;
    *) echo 'Enter Y or N.' ;;
  esac
done
if ! python3 -c 'import sys; name = sys.argv[1].strip(); sys.exit(0 if name and len(name) <= 150 and all(32 <= ord(char) <= 126 for char in name) else 1)' "$admin_username"; then
  echo "Enter a nonempty English-keyboard username (maximum 150 characters)." >&2; exit 1
fi
if [[ -z "$admin_password" || ${#admin_password} -gt 4096 || "$admin_password" != "$admin_confirmation" ]]; then
  echo "Administrator passwords must match and cannot be empty (maximum 4096 characters)." >&2; exit 1
fi
unset admin_confirmation use_default_admin
admin_username="$(python3 -c 'import sys; print(sys.argv[1].strip())' "$admin_username")"

local_test=0
email=""
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_dir/deploy/install-helpers.sh"
if [[ $# -eq 1 && "$1" == "--local-test" ]]; then
  local_test=1
  domain=localhost
elif [[ $# -ge 1 ]]; then
  domain="$1"
  if [[ $# -eq 2 ]]; then email="$2"; fi
else
  echo 'Detecting this server public IPv4 address...'
  domain="$(detect_public_ipv4)" || {
    echo 'Could not detect a public IPv4 address. Rerun the installer with a domain or IPv4 address as its argument.' >&2
    exit 1
  }
  echo "Detected panel address: $domain"
fi

valid_ipv4() {
  local part
  local -a parts
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  IFS=. read -r -a parts <<< "$1"
  for part in "${parts[@]}"; do
    (( 10#$part <= 255 )) || return 1
  done
}
if [[ $local_test -eq 0 ]] && ! valid_ipv4 "$domain" && \
   [[ ! "$domain" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]]; then
  echo "Enter a valid panel domain or IPv4 address." >&2; exit 1
fi
if [[ -n "$email" && ! "$email" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]]; then
  echo "Invalid email." >&2; exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx sudo rsync openssh-server openssl nftables

getent group sshvpn >/dev/null || groupadd --system sshvpn
getent group sshvpn-panel >/dev/null || groupadd --system sshvpn-panel
id sshvpn-panel >/dev/null 2>&1 || useradd --system --gid sshvpn-panel --home-dir /var/lib/sshvpn-panel --create-home --shell /usr/sbin/nologin sshvpn-panel
install -d -m 0755 -o root -g root /opt/ssh-vpn-panel
rsync -a --exclude='.git' --exclude='.venv' --exclude='.env' --exclude='staticfiles' --exclude='__pycache__' --exclude='frontend/node_modules' "$repo_dir/" /opt/ssh-vpn-panel/
chown -R root:root /opt/ssh-vpn-panel
python3 -m venv /opt/ssh-vpn-panel/.venv
/opt/ssh-vpn-panel/.venv/bin/pip install --no-cache-dir -r /opt/ssh-vpn-panel/requirements.txt

db_password="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 48)"
main_ssh_port="$(/usr/sbin/sshd -T | awk '$1 == "port" && !found {print $2; found=1}')"
panel_path="/$(openssl rand -hex 6)"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE ROLE sshvpn LOGIN PASSWORD '$db_password'"
runuser -u postgres -- createdb -O sshvpn sshvpn
install -d -m 0750 -o root -g sshvpn-panel /etc/sshvpn
cat > /etc/sshvpn/panel.env <<EOF
DJANGO_SECRET_KEY=$secret_key
PANEL_DOMAIN=$domain
PANEL_EMAIL=$email
PANEL_TLS_ENABLED=0
PANEL_HTTP_PORT=$([[ $local_test -eq 1 ]] && echo 8080 || echo 80)
PANEL_HTTPS_PORT=443
PANEL_WEB_PATH=$panel_path
VPN_SSH_PORT=$main_ssh_port
DB_NAME=sshvpn
DB_USER=sshvpn
DB_PASSWORD=$db_password
DB_HOST=127.0.0.1
DB_PORT=5432
EOF
chown root:sshvpn-panel /etc/sshvpn/panel.env
chmod 0640 /etc/sshvpn/panel.env

install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpnctl /usr/local/sbin/sshvpnctl
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_backup.py /usr/local/sbin/sshvpn-backup
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_policy.py /usr/local/sbin/sshvpn_policy.py
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_port.py /usr/local/sbin/sshvpn_port.py
install -m 0644 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_usage.py /usr/local/sbin/sshvpn_usage.py
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn_web.py /usr/local/sbin/sshvpn_web.py
install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpn-authz /usr/local/sbin/sshvpn-authz
install -d -m 0700 -o root -g root /etc/sshvpn/accounts
install -d -m 0750 -o root -g sshvpn-panel /var/lib/sshvpn-panel/backups
install -d -m 0700 -o sshvpn-panel -g sshvpn-panel /var/lib/sshvpn-panel/restore-uploads
install -m 0755 -o root -g root "$repo_dir/deploy/install-pam-hook.sh" /usr/local/sbin/sshvpn-install-pam-hook
printf '%s\n' 'sshvpn-panel ALL=(root) NOPASSWD: /usr/local/sbin/sshvpnctl, /usr/local/sbin/sshvpn-backup' > /etc/sudoers.d/sshvpn-panel
chmod 0440 /etc/sudoers.d/sshvpn-panel
visudo -cf /etc/sudoers.d/sshvpn-panel

install -m 0644 "$repo_dir/deploy/sshvpn-egress.nft" /etc/sshvpn/egress.nft
/usr/sbin/nft -c -f /etc/sshvpn/egress.nft
/usr/sbin/sshd -t
install -m 0644 "$repo_dir/deploy/sshvpn-egress.service" /etc/systemd/system/sshvpn-egress.service
install -m 0644 "$repo_dir/deploy/sshvpn-panel.service" /etc/systemd/system/sshvpn-panel.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.service" /etc/systemd/system/sshvpn-policy.service
install -m 0644 "$repo_dir/deploy/sshvpn-policy.timer" /etc/systemd/system/sshvpn-policy.timer
install -m 0644 "$repo_dir/deploy/sshvpn-usage.service" /etc/systemd/system/sshvpn-usage.service
install -m 0644 "$repo_dir/deploy/sshvpn-usage.timer" /etc/systemd/system/sshvpn-usage.timer
install -m 0755 -o root -g root "$repo_dir/deploy/configure-main-ssh.sh" /usr/local/sbin/sshvpn-configure-main-ssh
install -m 0755 -o root -g root "$repo_dir/deploy/menu.sh" /usr/local/bin/sshvpn-menu
if [[ ! -e /usr/local/bin/menu ]]; then
  ln -s /usr/local/bin/sshvpn-menu /usr/local/bin/menu
fi

PYTHONPATH=/opt/ssh-vpn-panel/helper python3 - "$domain" "$([[ $local_test -eq 1 ]] && echo 8080 || echo 80)" "$panel_path" "$local_test" > /etc/nginx/sites-available/sshvpn-panel <<'PY'
import sys
from sshvpn_web import nginx_config
print(nginx_config(sys.argv[1], int(sys.argv[2]), sys.argv[3], local_only=sys.argv[4] == "1"), end="")
PY
printf '%s\n' 'limit_req_zone $binary_remote_addr zone=sshvpn_login:10m rate=10r/m;' > /etc/nginx/conf.d/sshvpn-rate.conf
ln -s /etc/nginx/sites-available/sshvpn-panel /etc/nginx/sites-enabled/sshvpn-panel
nginx -t

set -a
source /etc/sshvpn/panel.env
set +a
cd /opt/ssh-vpn-panel
runuser -u sshvpn-panel -- .venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput
printf '%s\n%s\n' "$admin_username" "$admin_password" | runuser -u sshvpn-panel -- .venv/bin/python manage.py initial_admin

sshvpn-install-pam-hook
systemctl daemon-reload
systemctl enable --now sshvpn-egress
sshvpn-configure-main-ssh
systemctl enable --now postgresql nginx sshvpn-panel sshvpn-policy.timer sshvpn-usage.timer
systemctl reload nginx
echo
echo '============================================================'
echo '                 SSH VPN PANEL INSTALLED'
echo '============================================================'
if [[ $local_test -eq 1 ]]; then
  echo "Panel URL:       http://localhost:8080$panel_path/ (SSH tunnel required)"
else
  echo "Panel URL:       http://$domain$panel_path/"
fi
echo "Admin username:  $admin_username"
echo "Admin password:  $admin_password"
echo "VPN SSH port:    $main_ssh_port"
echo 'SSL later:       sudo menu ssl panel.example.com admin@example.com'
echo '============================================================'
unset admin_password
