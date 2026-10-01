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

read -r -p "Administrator username: " admin_username
read -r -s -p "Administrator password (at least 12 characters): " admin_password
echo
read -r -s -p "Confirm administrator password: " admin_confirmation
echo
if [[ ! "$admin_username" =~ ^[A-Za-z][A-Za-z0-9_]{2,31}$ ]]; then
  echo "Use 3-32 letters, digits or underscores; start with a letter." >&2; exit 1
fi
if [[ ${#admin_password} -lt 12 || ${#admin_password} -gt 256 || "$admin_password" != "$admin_confirmation" ]]; then
  echo "Administrator passwords must match and contain 12-256 characters." >&2; exit 1
fi
unset admin_confirmation

local_test=0
email=""
if [[ $# -eq 1 && "$1" == "--local-test" ]]; then
  local_test=1
  domain=localhost
elif [[ $# -ge 1 ]]; then
  domain="$1"
  if [[ $# -eq 2 ]]; then email="$2"; fi
else
  read -r -p "Panel domain or IPv4 address for HTTP: " domain
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

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx sudo rsync openssh-server openssl nftables

getent group sshvpn >/dev/null || groupadd --system sshvpn
getent group sshvpn-panel >/dev/null || groupadd --system sshvpn-panel
id sshvpn-panel >/dev/null 2>&1 || useradd --system --gid sshvpn-panel --home-dir /var/lib/sshvpn-panel --create-home --shell /usr/sbin/nologin sshvpn-panel
install -d -m 0755 -o root -g root /opt/ssh-vpn-panel
rsync -a --exclude='.git' --exclude='.venv' --exclude='.env' --exclude='staticfiles' --exclude='__pycache__' "$repo_dir/" /opt/ssh-vpn-panel/
chown -R root:root /opt/ssh-vpn-panel
python3 -m venv /opt/ssh-vpn-panel/.venv
/opt/ssh-vpn-panel/.venv/bin/pip install --no-cache-dir -r /opt/ssh-vpn-panel/requirements.txt

db_password="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 48)"
runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE ROLE sshvpn LOGIN PASSWORD '$db_password'"
runuser -u postgres -- createdb -O sshvpn sshvpn
install -d -m 0750 -o root -g sshvpn-panel /etc/sshvpn
cat > /etc/sshvpn/panel.env <<EOF
DJANGO_SECRET_KEY=$secret_key
PANEL_DOMAIN=$domain
PANEL_EMAIL=$email
PANEL_TLS_ENABLED=0
PANEL_HTTP_PORT=$([[ $local_test -eq 1 ]] && echo 8080 || echo 80)
DB_NAME=sshvpn
DB_USER=sshvpn
DB_PASSWORD=$db_password
DB_HOST=127.0.0.1
DB_PORT=5432
EOF
chown root:sshvpn-panel /etc/sshvpn/panel.env
chmod 0640 /etc/sshvpn/panel.env

install -m 0755 -o root -g root /opt/ssh-vpn-panel/helper/sshvpnctl /usr/local/sbin/sshvpnctl
printf '%s\n' 'sshvpn-panel ALL=(root) NOPASSWD: /usr/local/sbin/sshvpnctl' > /etc/sudoers.d/sshvpn-panel
chmod 0440 /etc/sudoers.d/sshvpn-panel
visudo -cf /etc/sudoers.d/sshvpn-panel

install -m 0644 "$repo_dir/deploy/sshvpn_sshd_config" /etc/ssh/sshvpn_sshd_config
install -m 0644 "$repo_dir/deploy/sshvpn-egress.nft" /etc/sshvpn/egress.nft
/usr/sbin/nft -c -f /etc/sshvpn/egress.nft
printf '%s\n' 'DenyGroups sshvpn' > /etc/ssh/sshd_config.d/05-sshvpn-deny.conf
chmod 0644 /etc/ssh/sshd_config.d/05-sshvpn-deny.conf
/usr/sbin/sshd -t
/usr/sbin/sshd -t -f /etc/ssh/sshvpn_sshd_config
main_ssh_policy="$(/usr/sbin/sshd -T)"
vpn_ssh_policy="$(/usr/sbin/sshd -T -f /etc/ssh/sshvpn_sshd_config)"
if ! grep -Eq '^denygroups (.* )?sshvpn( |$)' <<< "$main_ssh_policy" || \
   ! grep -Fxq 'allowgroups sshvpn' <<< "$vpn_ssh_policy" || \
   ! grep -Fxq 'maxsessions 0' <<< "$vpn_ssh_policy" || \
   ! grep -Fxq 'allowtcpforwarding local' <<< "$vpn_ssh_policy" || \
   ! grep -Fxq 'permittty no' <<< "$vpn_ssh_policy" || \
   ! grep -Fxq 'pubkeyauthentication no' <<< "$vpn_ssh_policy" || \
   ! grep -Fxq 'passwordauthentication yes' <<< "$vpn_ssh_policy"; then
  echo "SSH restrictions are not effective; refusing to start VPN services." >&2
  exit 1
fi
install -m 0644 "$repo_dir/deploy/sshvpn-sshd.service" /etc/systemd/system/sshvpn-sshd.service
install -m 0644 "$repo_dir/deploy/sshvpn-egress.service" /etc/systemd/system/sshvpn-egress.service
install -m 0644 "$repo_dir/deploy/sshvpn-panel.service" /etc/systemd/system/sshvpn-panel.service
install -m 0755 -o root -g root "$repo_dir/deploy/menu.sh" /usr/local/bin/sshvpn-menu
if [[ ! -e /usr/local/bin/menu ]]; then
  ln -s /usr/local/bin/sshvpn-menu /usr/local/bin/menu
fi

if [[ $local_test -eq 1 ]]; then
  nginx_listen='127.0.0.1:8080'
else
  nginx_listen='80'
fi
cat > /etc/nginx/sites-available/sshvpn-panel <<EOF
server {
    listen $nginx_listen;
    server_name $domain;
    location /static/ { alias /opt/ssh-vpn-panel/staticfiles/; }
    location = /login/ {
        limit_req zone=sshvpn_login burst=20 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
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
unset admin_password

systemctl daemon-reload
systemctl enable --now postgresql nginx sshvpn-panel sshvpn-egress sshvpn-sshd
systemctl reload nginx
systemctl reload ssh
if [[ $local_test -eq 1 ]]; then
  echo "Panel HTTP: http://localhost:8080/ through an administrator SSH tunnel."
else
  echo "Panel HTTP: http://$domain/"
fi
echo "Optional SSL later: sudo menu ssl panel.example.com admin@example.com"
echo "VPN SSH is bound to 127.0.0.1:2222 until egress isolation is tested. Keep your existing admin SSH port open."
