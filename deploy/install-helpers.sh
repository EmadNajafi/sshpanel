#!/usr/bin/env bash

public_ipv4() {
  python3 - "$1" <<'PY'
import ipaddress
import sys

try:
    address = ipaddress.ip_address(sys.argv[1].strip())
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if address.version == 4 and address.is_global else 1)
PY
}

detect_public_ipv4() {
  local candidate service

  if command -v ip >/dev/null 2>&1; then
    candidate="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}')" || candidate=""
    if [[ -n "$candidate" ]] && public_ipv4 "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  if command -v curl >/dev/null 2>&1; then
    for service in https://api.ipify.org https://ipv4.icanhazip.com; do
      candidate="$(curl --ipv4 --fail --silent --show-error --connect-timeout 3 --max-time 8 "$service" 2>/dev/null)" || continue
      candidate="${candidate//$'\r'/}"
      candidate="${candidate//$'\n'/}"
      if public_ipv4 "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done
  fi

  return 1
}

# Read /etc/sshvpn/panel.env as literal KEY=VALUE data. Never `source` it: the
# panel and backup restore can write this file, and root runs these scripts.
load_panel_env() {
  local line key
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^(DJANGO_SECRET_KEY|(PANEL|VPN|DB)_[A-Z0-9_]+)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    printf -v "$key" '%s' "${BASH_REMATCH[3]}"
    export "$key"
  done < "${1:-/etc/sshvpn/panel.env}"
}
