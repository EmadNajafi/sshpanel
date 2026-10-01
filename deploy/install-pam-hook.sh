#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
pam_file=/etc/pam.d/sshd
hook='account requisite pam_exec.so quiet /usr/local/sbin/sshvpn-authz'
if [[ ! -f "$pam_file" ]] || ! grep -Eq '^[[:space:]]*@include[[:space:]]+common-account' "$pam_file"; then
  echo 'Unexpected SSH PAM configuration; refusing to change it.' >&2
  exit 1
fi
if grep -Fqx "$hook" "$pam_file"; then exit 0; fi
if grep -Fq '/usr/local/sbin/sshvpn-authz' "$pam_file"; then
  echo 'An incompatible SSH VPN PAM hook already exists.' >&2
  exit 1
fi
backup="${1:-/var/backups/sshvpn-panel/pam-sshd-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
install -d -m 0700 -o root -g root "$(dirname "$backup")"
cp -a "$pam_file" "$backup"
temp="$(mktemp "${pam_file}.XXXXXX")"
trap 'rm -f "$temp"' EXIT
awk -v hook="$hook" '
  !added && /^[[:space:]]*@include[[:space:]]+common-account/ { print hook; added=1 }
  { print }
  END { if (!added) exit 1 }
' "$pam_file" > "$temp"
chown root:root "$temp"
chmod 0644 "$temp"
mv -f "$temp" "$pam_file"
echo "SSH PAM backup: $backup"
