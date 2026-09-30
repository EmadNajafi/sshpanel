#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "Run with sudo." >&2; exit 1; fi
source /etc/sshvpn/panel.env

show_menu() {
  echo "1) Issue or repair SSL certificate"
  echo "2) Renew SSL certificate"
  echo "3) Service status"
  echo "4) Restart panel and VPN SSH"
  echo "5) Show recent logs"
  echo "6) Back up PostgreSQL database"
  echo "0) Exit"
  read -r -p "Choose: " choice
  case "$choice" in
    1) issue_ssl ;;
    2) certbot renew ;;
    3) systemctl status --no-pager sshvpn-panel sshvpn-sshd nginx postgresql ;;
    4) systemctl restart sshvpn-panel sshvpn-sshd ;;
    5) journalctl -u sshvpn-panel -u sshvpn-sshd -n 100 --no-pager ;;
    6) backup_db ;;
    0) exit 0 ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
}

issue_ssl() {
  certbot --nginx -d "$PANEL_DOMAIN" --non-interactive --agree-tos --email "${PANEL_EMAIL:?PANEL_EMAIL missing}" --redirect
  nginx -t
  systemctl reload nginx
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
  *) echo "Usage: sshvpn-menu [menu|ssl|renew|status|restart|logs|backup]" >&2; exit 1 ;;
esac
