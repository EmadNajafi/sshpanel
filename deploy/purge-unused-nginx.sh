#!/usr/bin/env bash
# Remove Nginx only when it has no configuration belonging to another site.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --dry-run && $1 != --yes ) ]]; then
  echo 'Usage: bash purge-unused-nginx.sh [--dry-run|--yes]' >&2
  exit 1
fi

if ! dpkg-query -W -f='${db:Status-Status}' nginx-common 2>/dev/null | grep -qx installed; then
  echo 'Nginx is not installed.'
  exit 0
fi

if [[ -e /etc/nginx/sites-available/sshvpn-panel || -e /etc/nginx/sites-enabled/sshvpn-panel ]]; then
  echo 'The panel Nginx site is still present; run panel uninstall first.' >&2
  exit 1
fi

shopt -s nullglob
for entry in /etc/nginx/sites-enabled/*; do
  if [[ $entry != /etc/nginx/sites-enabled/default || ! -L $entry ||
        $(readlink "$entry") != /etc/nginx/sites-available/default ]]; then
    echo "Nginx serves another site ($entry); leaving it installed." >&2
    exit 1
  fi
done
for entry in /etc/nginx/sites-available/*; do
  if [[ $entry != /etc/nginx/sites-available/default ]]; then
    echo "Nginx has another site ($entry); leaving it installed." >&2
    exit 1
  fi
done
for entry in /etc/nginx/conf.d/*; do
  echo "Nginx has another configuration ($entry); leaving it installed." >&2
  exit 1
done
if [[ -d /etc/systemd/system/nginx.service.d ]]; then
  echo 'Nginx has custom service settings; leaving it installed.' >&2
  exit 1
fi
if [[ -n $(dpkg -V nginx-common 2>&1) ]]; then
  echo 'Packaged Nginx configuration was modified; leaving it installed.' >&2
  exit 1
fi

packages=()
for package in nginx nginx-core nginx-common; do
  if dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
    packages+=("$package")
  fi
done
echo "Nginx has no other site. Installed packages to purge: ${packages[*]}"
if [[ ${1:-} == --dry-run ]]; then
  echo 'Dry run only; nothing was deleted.'
  exit 0
fi
if [[ ${1:-} != --yes ]]; then
  if [[ ! -t 0 ]]; then echo 'An interactive terminal is required.' >&2; exit 1; fi
  read -r -p 'Type PURGE NGINX to continue: ' answer
  if [[ $answer != 'PURGE NGINX' ]]; then echo 'Cancelled.'; exit 1; fi
fi

systemctl disable --now nginx.service
DEBIAN_FRONTEND=noninteractive apt-get purge -y "${packages[@]}"
rm -rf -- /etc/nginx /var/log/nginx /var/lib/nginx
echo 'Unused Nginx was removed.'
