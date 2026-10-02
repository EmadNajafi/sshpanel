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
