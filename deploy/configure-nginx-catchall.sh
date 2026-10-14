#!/usr/bin/env bash
# Replace only Ubuntu's stock default page; leave other named sites untouched.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_file="$repo_dir/deploy/nginx-catchall.conf"
site=/etc/nginx/sites-available/sshvpn-catchall
enabled=/etc/nginx/sites-enabled/sshvpn-catchall
default_link=/etc/nginx/sites-enabled/default
default_site=/etc/nginx/sites-available/default
marker=/etc/sshvpn/default-nginx-site.disabled-by-panel

if [[ -e $site || -L $site || -e $enabled || -L $enabled ]]; then
  if [[ -f $site && -L $enabled && $(readlink "$enabled") == "$site" ]] &&
     cmp -s "$source_file" "$site"; then
    exit 0
  fi
  echo 'A custom Nginx catch-all exists; leaving it unchanged.' >&2
  exit 0
fi

disabled_default=0
if [[ -e $default_link || -L $default_link ]]; then
  if [[ ! -L $default_link || $(readlink "$default_link") != "$default_site" ]]; then
    echo 'A custom default Nginx site exists; leaving it unchanged.' >&2
    exit 0
  fi
  if dpkg -V nginx-common 2>/dev/null | grep -Fq " $default_site"; then
    echo 'The Ubuntu default Nginx site was customized; leaving it unchanged.' >&2
    exit 0
  fi
  rm -- "$default_link"
  disabled_default=1
fi

install -m 0644 -o root -g root "$source_file" "$site"
ln -s "$site" "$enabled"
if ! nginx -t; then
  rm -- "$enabled" "$site"
  if (( disabled_default )); then ln -s "$default_site" "$default_link"; fi
  nginx -t
  echo 'The Nginx catch-all conflicts with another site; previous configuration restored.' >&2
  exit 0
fi

if (( disabled_default )); then
  install -d -m 0750 -o root -g root /etc/sshvpn
  printf '%s\n' "$default_site" > "$marker"
  chmod 0600 "$marker"
fi
echo 'Nginx default requests now close without a response.'
