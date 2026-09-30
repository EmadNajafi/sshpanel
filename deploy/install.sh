#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run as root." >&2; exit 1; fi
if [[ $# -ne 2 ]]; then echo "Usage: sudo bash deploy/install.sh panel.example.com admin@example.com" >&2; exit 1; fi
domain="$1"
email="$2"
if [[ ! "$domain" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$ ]]; then echo "Invalid domain." >&2; exit 1; fi
if [[ ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then echo "Invalid email." >&2; exit 1; fi
if ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -q '^VERSION_ID="24.04"$' /etc/os-release; then
  echo "This installer targets Ubuntu 24.04 only." >&2; exit 1
fi
if ! grep -Eq '^Include /etc/ssh/sshd_config.d/\*\.conf' /etc/ssh/sshd_config; then
  echo "Main sshd does not include sshd_config.d; refusing to install." >&2; exit 1
fi
if [[ -e /opt/ssh-vpn-panel || -e /etc/sshvpn/panel.env ]]; then
  echo "Existing installation found; refusing to overwrite it." >&2; exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql nginx certbot python3-certbot-nginx sudo rsync openssh-server openssl

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
printf '%s\n' 'DenyGroups sshvpn' > /etc/ssh/sshd_config.d/05-sshvpn-deny.conf
chmod 0644 /etc/ssh/sshd_config.d/05-sshvpn-deny.conf
/usr/sbin/sshd -t
/usr/sbin/sshd -t -f /etc/ssh/sshvpn_sshd_config
install -m 0644 "$repo_dir/deploy/sshvpn-sshd.service" /etc/systemd/system/sshvpn-sshd.service
install -m 0644 "$repo_dir/deploy/sshvpn-panel.service" /etc/systemd/system/sshvpn-panel.service
install -m 0755 -o root -g root "$repo_dir/deploy/menu.sh" /usr/local/bin/sshvpn-menu
if [[ ! -e /usr/local/bin/menu ]]; then
  ln -s /usr/local/bin/sshvpn-menu /usr/local/bin/menu
fi

cat > /etc/nginx/sites-available/sshvpn-panel <<EOF
server {
    listen 80;
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

systemctl daemon-reload
systemctl enable --now postgresql nginx sshvpn-panel sshvpn-sshd
systemctl reload ssh
echo "Create the web administrator now:"
runuser -u sshvpn-panel -- .venv/bin/python manage.py createsuperuser
echo "Issue TLS: sudo menu ssl"
echo "VPN SSH is bound to 127.0.0.1:2222 until egress isolation is tested. Keep your existing admin SSH port open."
