#!/usr/bin/env bash
# Install or refresh the dedicated SSH listener for VPN accounts.
#   install-vpn-sshd.sh PORT      write the config for PORT and (re)start the service
#   install-vpn-sshd.sh --pick    print the current VPN port, or a free one from 2222-2299
set -euo pipefail

if [[ $EUID -ne 0 || $# -ne 1 ]]; then
  echo 'Usage: sudo bash deploy/install-vpn-sshd.sh PORT|--pick' >&2
  exit 1
fi
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config=/etc/ssh/sshvpn_sshd_config
marker='# Managed by sshvpn-panel: dedicated SSH listener for VPN accounts only.'

main_ports() { /usr/sbin/sshd -T | awk '$1 == "port" { print $2 }'; }
listening() { [[ -n "$(ss -Hltn "sport = :$1" 2>/dev/null)" ]]; }

if [[ $1 == --pick ]]; then
  # Keep the port of an existing managed listener so clients keep working.
  if [[ -f $config ]] && head -n 1 "$config" | grep -Fxq "$marker"; then
    existing="$(awk '$1 == "Port" { print $2; exit }' "$config")"
    if [[ $existing =~ ^[0-9]+$ ]]; then echo "$existing"; exit 0; fi
  fi
  mapfile -t taken < <(main_ports)
  for candidate in $(seq 2222 2299); do
    if [[ " ${taken[*]} " != *" $candidate "* ]] && ! listening "$candidate"; then
      echo "$candidate"
      exit 0
    fi
  done
  echo 'No free port between 2222 and 2299 for the VPN SSH listener.' >&2
  exit 1
fi

port="$1"
if [[ ! $port =~ ^[0-9]+$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
  echo 'The VPN SSH port must be between 1 and 65535.' >&2
  exit 1
fi
port="$((10#$port))"
if (( port == 80 || port == 443 )); then
  echo 'Ports 80 and 443 are reserved for the web panel and certificates.' >&2
  exit 1
fi
if main_ports | grep -Fxq "$port"; then
  echo "Port $port is the administrator SSH port; choose a separate VPN port." >&2
  exit 1
fi

previous=""
if [[ -f $config ]]; then previous="$(mktemp)"; cp -a "$config" "$previous"; fi
restore() {
  if [[ -n $previous ]]; then cp -a "$previous" "$config"; rm -f "$previous"; else rm -f "$config"; fi
}
sed "s/__VPN_PORT__/$port/" "$repo_dir/deploy/sshvpn_sshd_config" > "$config.tmp"
chown root:root "$config.tmp"
chmod 0644 "$config.tmp"
mv -f "$config.tmp" "$config"
if ! /usr/sbin/sshd -t -f "$config"; then
  restore
  echo 'The VPN SSH configuration failed validation; nothing was started.' >&2
  exit 1
fi
install -m 0644 -o root -g root "$repo_dir/deploy/sshvpn-sshd.service" /etc/systemd/system/sshvpn-sshd.service
systemctl daemon-reload
systemctl enable sshvpn-sshd.service >/dev/null
if ! systemctl is-active --quiet sshvpn-sshd.service && listening "$port"; then
  restore
  echo "Another service already listens on port $port." >&2
  exit 1
fi
systemctl reload-or-restart sshvpn-sshd.service
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow "$port/tcp" >/dev/null
fi
for _ in $(seq 1 20); do
  if listening "$port"; then
    rm -f "${previous:-/nonexistent}"
    echo "VPN SSH listener is active on port $port."
    exit 0
  fi
  sleep 0.5
done
restore
systemctl reload-or-restart sshvpn-sshd.service || true
echo "The VPN SSH listener did not start on port $port; see journalctl -u sshvpn-sshd." >&2
exit 1
