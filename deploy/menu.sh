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
  echo "0) Exit"
  read -r -p "Choose: " choice
  case "$choice" in
    1) issue_ssl ;;
    2) certbot renew ;;
    3) systemctl status --no-pager sshvpn-panel sshvpn-sshd nginx postgresql ;;
    4) systemctl restart sshvpn-panel sshvpn-sshd ;;
    5) journalctl -u sshvpn-panel -u sshvpn-sshd -n 100 --no-pager ;;
    6) backup_db ;;
    7) create_admin ;;
    8) read -r -p "Administrator username: " admin_user; change_admin_password "$admin_user" ;;
    9) vpn_listen public ;;
    10) vpn_listen local ;;
    0) exit 0 ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
}

issue_ssl() {
  if [[ "${PANEL_TLS_ENABLED:-1}" != "1" ]]; then
    echo "Local test mode has no public domain. Configure a domain before requesting SSL." >&2
    exit 1
  fi
  certbot --nginx -d "$PANEL_DOMAIN" --non-interactive --agree-tos --email "${PANEL_EMAIL:?PANEL_EMAIL missing}" --redirect
  nginx -t
  systemctl reload nginx
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
  ssl) issue_ssl ;;
  renew) certbot renew ;;
  status) systemctl status --no-pager sshvpn-panel sshvpn-sshd nginx postgresql ;;
  restart) systemctl restart sshvpn-panel sshvpn-sshd ;;
  logs) journalctl -u sshvpn-panel -u sshvpn-sshd -n 100 --no-pager ;;
  backup) backup_db ;;
  admin) create_admin ;;
  admin-password) shift; change_admin_password "$@" ;;
  vpn-public) vpn_listen public ;;
  vpn-local) vpn_listen local ;;
  *) echo "Usage: sshvpn-menu [menu|ssl|renew|status|restart|logs|backup|admin|admin-password USERNAME|vpn-public|vpn-local]" >&2; exit 1 ;;
esac
