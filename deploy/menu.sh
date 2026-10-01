#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
set -a
source /etc/sshvpn/panel.env
set +a

show_menu() {
  echo "1) Issue or repair SSL certificate"
  echo "2) Renew SSL certificate"
  echo "3) Service status"
  echo "4) Restart panel and VPN SSH"
  echo "5) Show recent logs"
  echo "6) Back up PostgreSQL database"
  echo "7) Create a web administrator"
  echo "8) Change a web administrator password"
  echo "9) Open VPN SSH port to clients"
  echo "10) Restrict VPN SSH port to this server"
  echo "11) Open the web panel on this server's public IPv4 (HTTP)"
  echo "0) Exit"
  read -r -p "Choose: " choice
  case "$choice" in
    1)
      if [[ "${PANEL_TLS_ENABLED:-1}" == "0" ]]; then
        read -r -p "Panel domain: " requested_domain
        read -r -p "Certificate email: " requested_email
        issue_ssl "$requested_domain" "$requested_email"
      else
        issue_ssl
      fi
      ;;
    2) certbot renew ;;
    3) systemctl status --no-pager sshvpn-panel sshvpn-sshd sshvpn-policy.timer nginx postgresql ;;
    4) systemctl restart sshvpn-panel sshvpn-sshd ;;
    5) journalctl -u sshvpn-panel -u sshvpn-sshd -u sshvpn-policy.service -n 100 --no-pager ;;
    6) backup_db ;;
    7) create_admin ;;
    8) read -r -p "Administrator username: " admin_user; change_admin_password "$admin_user" ;;
    9) vpn_listen public ;;
    10) vpn_listen local ;;
    11) read -r -p "Server public IPv4: " panel_ip; web_public "$panel_ip" ;;
    0) exit 0 ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
}

web_public() {
  local ip="${1:-}" nginx_config=/etc/nginx/sites-available/sshvpn-panel
  local env_file=/etc/sshvpn/panel.env nginx_backup env_backup candidate attempt
  if [[ $# -ne 1 || "${PANEL_TLS_ENABLED:-1}" != "0" ]]; then
    echo "Usage: sudo menu web-public SERVER_PUBLIC_IPV4 (HTTP installations only)" >&2
    return 1
  fi
  if ! python3 - "$ip" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.IPv4Address(sys.argv[1])
except ipaddress.AddressValueError:
    sys.exit(1)
sys.exit(0 if address.is_global else 1)
PY
  then
    echo "Enter a public IPv4 address." >&2
    return 1
  fi
  if ! grep -Eq '^[[:space:]]*listen[[:space:]]+(127\.0\.0\.1:8080|80);' "$nginx_config" || \
     ! grep -q '^PANEL_DOMAIN=' "$env_file"; then
    echo "Unexpected panel configuration; no changes made." >&2
    return 1
  fi

  nginx_backup="$(mktemp)"
  env_backup="$(mktemp)"
  candidate="$(mktemp)"
  cp "$nginx_config" "$nginx_backup"
  cp "$env_file" "$env_backup"
  sed -E -e 's/listen 127\.0\.0\.1:8080;/listen 80;/' \
      -e "s/server_name [^;]*;/server_name $ip;/" "$nginx_config" > "$candidate"
  if ! grep -Eq '^[[:space:]]*listen[[:space:]]+80;' "$candidate" || \
     ! grep -Fq "server_name $ip;" "$candidate"; then
    rm -f "$nginx_backup" "$env_backup" "$candidate"
    echo "Could not prepare the public HTTP configuration; no changes made." >&2
    return 1
  fi
  install -m 0644 -o root -g root "$candidate" "$nginx_config"
  sed -i -e "s/^PANEL_DOMAIN=.*/PANEL_DOMAIN=$ip/" \
      -e 's/^PANEL_HTTP_PORT=.*/PANEL_HTTP_PORT=80/' "$env_file"
  if ! grep -q '^PANEL_HTTP_PORT=' "$env_file"; then
    printf '%s\n' 'PANEL_HTTP_PORT=80' >> "$env_file"
  fi
  chown root:sshvpn-panel "$env_file"
  chmod 0640 "$env_file"

  if ! nginx -t || ! systemctl restart sshvpn-panel || ! systemctl reload nginx; then
    install -m 0644 -o root -g root "$nginx_backup" "$nginx_config"
    install -m 0640 -o root -g sshvpn-panel "$env_backup" "$env_file"
    systemctl reload nginx || true
    systemctl restart sshvpn-panel || true
    rm -f "$nginx_backup" "$env_backup" "$candidate"
    echo "Public HTTP activation failed; previous panel settings were restored." >&2
    return 1
  fi
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if python3 - "$ip" <<'PY' 2>/dev/null
import http.client
import sys
connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
connection.request("GET", "/login/", headers={"Host": sys.argv[1]})
response = connection.getresponse()
sys.exit(0 if response.status == 200 else 1)
PY
    then
      rm -f "$nginx_backup" "$env_backup" "$candidate"
      echo "Panel HTTP is active at http://$ip/login/"
      echo "If it is unreachable from outside, allow inbound TCP 80 in the server and cloud firewalls."
      return 0
    fi
    sleep 1
  done
  install -m 0644 -o root -g root "$nginx_backup" "$nginx_config"
  install -m 0640 -o root -g sshvpn-panel "$env_backup" "$env_file"
  systemctl reload nginx || true
  systemctl restart sshvpn-panel || true
  rm -f "$nginx_backup" "$env_backup" "$candidate"
  echo "Public HTTP did not answer on port 80; previous panel settings were restored." >&2
  return 1
}

issue_ssl() {
  if [[ "${PANEL_TLS_ENABLED:-1}" == "0" ]]; then
    if [[ $# -ne 2 ]]; then
      echo "Usage: sudo menu ssl panel.example.com admin@example.com" >&2
      return 1
    fi
    activate_tls "$1" "$2"
    return
  fi
  if [[ $# -ne 0 ]]; then
    echo "This installation already uses $PANEL_DOMAIN. Run sudo menu ssl without arguments." >&2
    return 1
  fi
  certbot --nginx -d "$PANEL_DOMAIN" --non-interactive --agree-tos --email "${PANEL_EMAIL:?PANEL_EMAIL missing}" --redirect
  nginx -t
  systemctl reload nginx
}

activate_tls() {
  local domain="$1" email="$2" nginx_config env_file nginx_backup env_backup candidate
  if [[ ! "$domain" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}$ ]]; then
    echo "Invalid domain." >&2
    return 1
  fi
  if [[ ! "$email" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
    echo "Invalid email." >&2
    return 1
  fi

  nginx_config=/etc/nginx/sites-available/sshvpn-panel
  env_file=/etc/sshvpn/panel.env
  nginx_backup="$(mktemp)"
  env_backup="$(mktemp)"
  candidate="$(mktemp)"
  cp "$nginx_config" "$nginx_backup"
  cp "$env_file" "$env_backup"
  if ! sed -i -e "s/^PANEL_DOMAIN=.*/PANEL_DOMAIN=$domain/" \
      -e "s/^PANEL_EMAIL=.*/PANEL_EMAIL=$email/" \
      -e 's/^PANEL_TLS_ENABLED=.*/PANEL_TLS_ENABLED=1/' "$env_file" || \
     ! chown root:sshvpn-panel "$env_file" || ! chmod 0640 "$env_file" || \
     ! systemctl restart sshvpn-panel; then
    install -m 0640 -o root -g sshvpn-panel "$env_backup" "$env_file"
    systemctl restart sshvpn-panel || true
    rm -f "$nginx_backup" "$env_backup" "$candidate"
    echo "Panel activation failed; local panel settings were restored." >&2
    return 1
  fi
  sed -e 's/listen 127.0.0.1:8080;/listen 80;/' \
      -e "s/server_name [^;]*;/server_name $domain;/" "$nginx_config" > "$candidate"
  install -m 0644 -o root -g root "$candidate" "$nginx_config"
  rm -f "$candidate"

  if ! nginx -t || ! systemctl reload nginx || \
     ! certbot --nginx -d "$domain" --non-interactive --agree-tos --email "$email" --redirect; then
    install -m 0640 -o root -g sshvpn-panel "$env_backup" "$env_file"
    install -m 0644 -o root -g root "$nginx_backup" "$nginx_config"
    systemctl reload nginx || true
    systemctl restart sshvpn-panel || true
    rm -f "$nginx_backup" "$env_backup"
    echo "Certificate request failed; local panel settings were restored." >&2
    return 1
  fi
  rm -f "$nginx_backup" "$env_backup"
  echo "Panel HTTPS is active at https://$domain/"
}

create_admin() {
  cd /opt/ssh-vpn-panel
  runuser -u sshvpn-panel -- .venv/bin/python manage.py createsuperuser
}

change_admin_password() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: sudo menu admin-password USERNAME" >&2
    exit 1
  fi
  cd /opt/ssh-vpn-panel
  runuser -u sshvpn-panel -- .venv/bin/python manage.py changepassword "$1"
}

vpn_listen() {
  local mode="$1" address config temp
  config=/etc/ssh/sshvpn_sshd_config
  if [[ "$mode" == public ]]; then
    systemctl is-active --quiet sshvpn-egress
    /usr/sbin/nft list table inet sshvpn_egress >/dev/null
    address=0.0.0.0
  else
    address=127.0.0.1
  fi
  temp="$(mktemp)"
  sed -E "s/^ListenAddress (127\.0\.0\.1|0\.0\.0\.0)$/ListenAddress $address/" "$config" > "$temp"
  /usr/sbin/sshd -t -f "$temp"
  install -m 0644 -o root -g root "$temp" "$config"
  rm -f "$temp"
  systemctl restart sshvpn-sshd
  echo "VPN SSH now listens on $address:2222"
}

backup_db() {
  install -d -m 0700 -o root -g root /var/backups/sshvpn-panel
  backup="/var/backups/sshvpn-panel/sshvpn-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
  runuser -u postgres -- pg_dump sshvpn | gzip -9 > "$backup"
  chmod 0600 "$backup"
  echo "Saved $backup"
}

case "${1:-menu}" in
  menu) show_menu ;;
  ssl) shift; issue_ssl "$@" ;;
  renew) certbot renew ;;
  status) systemctl status --no-pager sshvpn-panel sshvpn-sshd sshvpn-policy.timer nginx postgresql ;;
  restart) systemctl restart sshvpn-panel sshvpn-sshd ;;
  logs) journalctl -u sshvpn-panel -u sshvpn-sshd -u sshvpn-policy.service -n 100 --no-pager ;;
  backup) backup_db ;;
  admin) create_admin ;;
  admin-password) shift; change_admin_password "$@" ;;
  vpn-public) vpn_listen public ;;
  vpn-local) vpn_listen local ;;
  web-public) shift; web_public "$@" ;;
  *) echo "Usage: sshvpn-menu [menu|ssl [DOMAIN EMAIL]|renew|status|restart|logs|backup|admin|admin-password USERNAME|vpn-public|vpn-local|web-public SERVER_PUBLIC_IPV4]" >&2; exit 1 ;;
esac
