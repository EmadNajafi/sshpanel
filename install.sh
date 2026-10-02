#!/usr/bin/env bash
# Public one-command bootstrap. Run in an interactive root shell on Ubuntu 24.04.
set -euo pipefail

repository=https://github.com/EmadNajafi/sshpanel.git
checkout=/root/sshpanel

if [[ $EUID -ne 0 ]]; then
  echo 'Run this command from a root shell (sudo -i).' >&2
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
installed=0
if [[ -e /opt/ssh-vpn-panel && -f /etc/sshvpn/panel.env ]]; then
  installed=1
elif [[ -e /opt/ssh-vpn-panel || -e /etc/sshvpn/panel.env ]]; then
  echo 'An incomplete installation exists. Review it before retrying; no data was changed.' >&2
  exit 1
fi
if [[ $installed -eq 1 && $# -ne 0 ]]; then
  echo 'An existing installation is upgraded without a host or email argument.' >&2
  exit 1
fi
if [[ $installed -eq 0 && ! -t 0 ]]; then
  echo 'Run a fresh installation in an interactive terminal to choose administrator credentials.' >&2
  exit 1
fi
printf '\033[H\033[2J'
if [[ $installed -eq 1 ]]; then
  echo 'SSH VPN Panel upgrade'
else
  echo 'SSH VPN Panel installer'
fi
echo 'Preparing the panel files...'
if ! command -v git >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git ca-certificates
fi
if [[ -e $checkout ]]; then
  if [[ ! -d $checkout/.git ]]; then
    echo "The checkout path $checkout already exists and is not this repository. Review it before installing." >&2
    exit 1
  fi
  case "$(git -C "$checkout" remote get-url origin)" in
    "$repository") ;;
    git@github.com:EmadNajafi/sshpanel.git|ssh://git@ssh.github.com:443/EmadNajafi/sshpanel.git)
      git -C "$checkout" remote set-url origin "$repository" ;;
    *) echo "The checkout path $checkout points to a different repository. Review it before continuing." >&2; exit 1 ;;
  esac
  git -C "$checkout" pull --ff-only
else
  git clone --depth 1 --branch main "$repository" "$checkout"
fi
if [[ $installed -eq 1 ]]; then
  bash "$checkout/deploy/upgrade.sh"
else
  bash "$checkout/deploy/install.sh" "$@"
fi
