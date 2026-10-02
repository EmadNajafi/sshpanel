#!/usr/bin/env bash
# Public one-command bootstrap. Run in an interactive root shell on Ubuntu 24.04.
set -euo pipefail

repository=https://github.com/EmadNajafi/sshpanel.git
checkout=/root/sshpanel

if [[ $EUID -ne 0 ]]; then
  echo 'Run this command from a root shell (sudo -i).' >&2
  exit 1
fi
if [[ ! -t 0 ]]; then
  echo 'Run this installer in an interactive terminal; it prompts for administrator credentials.' >&2
  exit 1
fi
if (( $# > 2 )); then
  echo 'Usage: bash <(curl -fsSL --ipv4 https://raw.githubusercontent.com/EmadNajafi/sshpanel/main/install.sh) [PANEL_HOST [EMAIL]]' >&2
  exit 1
fi
if ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -Eq '^VERSION_ID="?24\.04"?$' /etc/os-release; then
  echo 'This installer supports Ubuntu 24.04 only.' >&2
  exit 1
fi
if [[ -e /opt/ssh-vpn-panel || -e /etc/sshvpn/panel.env ]]; then
  echo 'An installation already exists. Use the documented upgrade procedure; no data was changed.' >&2
  exit 1
fi
if [[ -e $checkout ]]; then
  echo "The checkout path $checkout already exists. Review it before installing." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates
fi
git clone --depth 1 --branch main "$repository" "$checkout"
bash "$checkout/deploy/install.sh" "$@"
