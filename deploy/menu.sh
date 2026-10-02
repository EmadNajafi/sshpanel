#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
set -a
source /etc/sshvpn/panel.env
set +a

panel_url() {
  local scheme=http port="${PANEL_HTTP_PORT:-80}" default_port=80
  if [[ "${PANEL_TLS_ENABLED:-0}" == "1" ]]; then
    scheme=https
    port="${PANEL_HTTPS_PORT:-443}"
    default_port=443
  fi
  if [[ "$port" == "$default_port" ]]; then
    printf '%s://%s%s/\n' "$scheme" "$PANEL_DOMAIN" "${PANEL_WEB_PATH:-}"
  else
    printf '%s://%s:%s%s/\n' "$scheme" "$PANEL_DOMAIN" "$port" "${PANEL_WEB_PATH:-}"
  fi
}

admin_usernames() {
  (cd /opt/ssh-vpn-panel && runuser -u sshvpn-panel -- .venv/bin/python manage.py shell --no-imports -c \
    'from django.contrib.auth import get_user_model; print(", ".join(get_user_model().objects.filter(is_staff=True, is_active=True).order_by("username").values_list("username", flat=True)))' \
    2>/dev/null) || true
}

show_menu() {
  local reset=$'\033[0m' blue=$'\033[1;34m' cyan=$'\033[1;36m'
  local green=$'\033[1;32m' yellow=$'\033[1;33m' magenta=$'\033[1;35m'
  local dim=$'\033[2m' choice admins
  if [[ ! -t 1 || -n "${NO_COLOR:-}" ]]; then
    reset='' blue='' cyan='' green='' yellow='' magenta='' dim=''
  fi
  admins="$(admin_usernames)"
  [[ -n "$admins" ]] || admins='None available'
  printf '\n%s+------------------------------------------------------------+%s\n' "$blue" "$reset"
  printf '%s|  SSH VPN PANEL                                SERVER MENU  |%s\n' "$blue" "$reset"
  printf '%s+------------------------------------------------------------+%s\n' "$blue" "$reset"
  printf '  %sPANEL ACCESS%s\n' "$cyan" "$reset"
  printf '  Address   %s%s%s\n' "$green" "$(panel_url)" "$reset"
  printf '  Admin     %s%s%s\n' "$green" "$admins" "$reset"
  printf '  Password  %sNot recoverable (stored as a hash); use option 7%s\n' "$dim" "$reset"
  printf '\n  %sCERTIFICATES%s                 %sOPERATIONS%s\n' "$yellow" "$reset" "$magenta" "$reset"
  printf '   1  Issue or repair SSL         3  Service status\n'
  printf '   2  Renew SSL certificate       4  Restart panel and SSH\n'
  printf '                                 5  Recent logs\n'
  printf '                                 6  Back up database\n'
  printf '\n  %sADMINISTRATION%s\n' "$cyan" "$reset"
  printf '   7  Change admin password\n'
  printf '   8  Publish panel on IPv4 (HTTP only)\n'
  printf '   9  Completely uninstall panel\n'
  printf '\n   0  Exit\n\n'
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
    3) systemctl status --no-pager sshvpn-panel ssh sshvpn-egress sshvpn-udpgw sshvpn-policy.timer nginx postgresql ;;
    4) systemctl restart sshvpn-panel sshvpn-udpgw; /usr/sbin/sshd -t && systemctl reload ssh ;;
    5) journalctl -u sshvpn-panel -u ssh -u sshvpn-udpgw -u sshvpn-policy.service -n 100 --no-pager ;;
    6) backup_db ;;
    7) read -r -p "Administrator username: " admin_user; change_admin_password "$admin_user" ;;
    8) read -r -p "Server public IPv4: " panel_ip; web_public "$panel_ip" ;;
    9) bash /opt/ssh-vpn-panel/deploy/uninstall.sh ;;
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
  if ! grep -Eq '^[[:space:]]*listen[[:space:]]+(127\.0\.0\.1:)?[0-9]+;' "$nginx_config" || \
     ! grep -q '^PANEL_DOMAIN=' "$env_file"; then
    echo "Unexpected panel configuration; no changes made." >&2
    return 1
  fi

  nginx_backup="$(mktemp)"
  env_backup="$(mktemp)"
  candidate="$(mktemp)"
  cp "$nginx_config" "$nginx_backup"
  cp "$env_file" "$env_backup"
  sed -E -e 's/listen (127\.0\.0\.1:)?[0-9]+;/listen 80;/' \
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
    if python3 - "$ip" "${PANEL_WEB_PATH:-}" <<'PY' 2>/dev/null
import http.client
import sys
connection = http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
connection.request("GET", sys.argv[2] + "/login/", headers={"Host": sys.argv[1]})
response = connection.getresponse()
sys.exit(0 if response.status == 200 else 1)
PY
    then
      rm -f "$nginx_backup" "$env_backup" "$candidate"
      echo "Panel HTTP is active at http://$ip${PANEL_WEB_PATH:-}/login/"
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
      -e 's/^PANEL_TLS_ENABLED=.*/PANEL_TLS_ENABLED=1/' \
      -e 's/^PANEL_HTTP_PORT=.*/PANEL_HTTP_PORT=80/' \
      -e 's/^PANEL_HTTPS_PORT=.*/PANEL_HTTPS_PORT=443/' "$env_file" || \
     ! chown root:sshvpn-panel "$env_file" || ! chmod 0640 "$env_file" || \
     ! systemctl restart sshvpn-panel; then
    install -m 0640 -o root -g sshvpn-panel "$env_backup" "$env_file"
    systemctl restart sshvpn-panel || true
    rm -f "$nginx_backup" "$env_backup" "$candidate"
    echo "Panel activation failed; local panel settings were restored." >&2
    return 1
  fi
  sed -E -e 's/listen (127\.0\.0\.1:)?[0-9]+;/listen 80;/' \
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
  echo "Panel HTTPS is active at https://$domain${PANEL_WEB_PATH:-}/"
}

change_admin_password() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: sudo menu admin-password USERNAME" >&2
    exit 1
  fi
  cd /opt/ssh-vpn-panel
  runuser -u sshvpn-panel -- .venv/bin/python manage.py changepassword "$1"
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
  status) systemctl status --no-pager sshvpn-panel ssh sshvpn-egress sshvpn-udpgw sshvpn-policy.timer nginx postgresql ;;
  restart) systemctl restart sshvpn-panel sshvpn-udpgw; /usr/sbin/sshd -t && systemctl reload ssh ;;
  logs) journalctl -u sshvpn-panel -u ssh -u sshvpn-policy.service -n 100 --no-pager ;;
  backup) backup_db ;;
  admin-password) shift; change_admin_password "$@" ;;
  vpn-public|vpn-local) echo 'VPN accounts now use the main SSH port; no separate VPN listener exists.' >&2; exit 1 ;;
  web-public) shift; web_public "$@" ;;
  uninstall) shift; bash /opt/ssh-vpn-panel/deploy/uninstall.sh "$@" ;;
  *) echo "Usage: sshvpn-menu [menu|ssl [DOMAIN EMAIL]|renew|status|restart|logs|backup|admin-password USERNAME|web-public SERVER_PUBLIC_IPV4|uninstall [--dry-run]]" >&2; exit 1 ;;
esac
