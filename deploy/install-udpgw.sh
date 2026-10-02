#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 || $# -ne 1 ]]; then
  echo 'Usage: sudo bash deploy/install-udpgw.sh PORT (1024-65535)' >&2
  exit 1
fi
if [[ ! $1 =~ ^[0-9]+$ ]] || (( 10#$1 < 1024 || 10#$1 > 65535 )); then
  echo 'Usage: sudo bash deploy/install-udpgw.sh PORT (1024-65535)' >&2
  exit 1
fi

port="$1"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
binary=/usr/local/libexec/sshvpn-badvpn-udpgw
upstream_commit=07268f02706e78e282e19641b5d1d41e8e89bf31

getent group sshvpn-udpgw >/dev/null || groupadd --system sshvpn-udpgw
id sshvpn-udpgw >/dev/null 2>&1 || useradd --system --gid sshvpn-udpgw \
  --home-dir /var/empty --no-create-home --shell /usr/sbin/nologin sshvpn-udpgw

if [[ ! -x $binary ]]; then
  if ! command -v git >/dev/null || ! command -v cmake >/dev/null || ! command -v cc >/dev/null; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y git cmake build-essential
  fi
  for tool in git cmake cc; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 1; }
  done
  build_dir="$(mktemp -d)"
  trap 'rm -rf -- "$build_dir"' EXIT
  git clone --quiet --depth 1 https://github.com/ambrop72/badvpn.git "$build_dir/source"
  if [[ $(git -C "$build_dir/source" rev-parse HEAD) != "$upstream_commit" ]]; then
    echo 'Unexpected BadVPN source revision; refusing to build.' >&2
    exit 1
  fi
  cmake -S "$build_dir/source" -B "$build_dir/build" -DBUILD_NOTHING_BY_DEFAULT=1 \
    -DBUILD_UDPGW=1 -DCMAKE_BUILD_TYPE=Release
  cmake --build "$build_dir/build" --target badvpn-udpgw -j 2
  install -d -m 0755 -o root -g root /usr/local/libexec
  install -m 0755 -o root -g root "$build_dir/build/udpgw/badvpn-udpgw" "$binary"
  trap - EXIT
  rm -rf -- "$build_dir"
fi

install -d -m 0750 -o root -g sshvpn-panel /etc/sshvpn
sed "s/__UDPGW_PORT__/$port/g" "$repo_dir/deploy/sshvpn-egress.nft" > /etc/sshvpn/egress.nft
chown root:root /etc/sshvpn/egress.nft
chmod 0644 /etc/sshvpn/egress.nft
/usr/sbin/nft -c -f /etc/sshvpn/egress.nft

sed "s/__UDPGW_PORT__/$port/g" "$repo_dir/deploy/sshvpn-udpgw.service" > /etc/systemd/system/sshvpn-udpgw.service
chown root:root /etc/systemd/system/sshvpn-udpgw.service
chmod 0644 /etc/systemd/system/sshvpn-udpgw.service
