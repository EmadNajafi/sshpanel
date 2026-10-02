#!/usr/bin/env bash
# Remove panel-created Linux VPN accounts, including ones left by an older uninstall.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo 'Run as root.' >&2; exit 1; fi
dry_run=0
confirmed=0
keep_group=0
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=1 ;;
    --yes) confirmed=1 ;;
    --keep-group) keep_group=1 ;;
    *) echo 'Usage: bash cleanup-vpn-users.sh [--dry-run|--yes] [--keep-group]' >&2; exit 1 ;;
  esac
done
if (( dry_run && confirmed )); then
  echo 'Choose either --dry-run or --yes.' >&2
  exit 1
fi
if (( ! keep_group && ! dry_run )) &&
   [[ -f /etc/sshvpn/panel.env || -d /opt/ssh-vpn-panel ]]; then
  echo 'The panel is still installed; use sudo menu uninstall to remove it and its accounts.' >&2
  exit 1
fi

if ! getent group sshvpn >/dev/null; then
  echo 'The sshvpn group is absent, so panel ownership cannot be verified.' >&2
  echo 'Accounts with the old panel home and shell, for manual review:' >&2
  getent passwd | awk -F: '$6 == "/var/empty" && $7 == "/usr/sbin/nologin" { print "  " $1 }' >&2
  exit 1
fi
list=$(mktemp)
trap 'rm -f -- "$list"' EXIT
python3 - > "$list" <<'PY'
import grp
import pwd

group = grp.getgrnam('sshvpn')
if group.gr_mem:
    raise SystemExit(f'sshvpn also contains supplementary members: {", ".join(group.gr_mem)}; no users were deleted.')
gid = group.gr_gid
for account in pwd.getpwall():
    if account.pw_gid != gid:
        continue
    if account.pw_dir != '/var/empty' or account.pw_shell != '/usr/sbin/nologin':
        raise SystemExit(f'Unexpected account in sshvpn group: {account.pw_name}; no users were deleted.')
    print(f'{account.pw_name}:{account.pw_uid}')
PY
mapfile -t accounts < "$list"
echo "Panel VPN accounts found: ${#accounts[@]}"
for account in "${accounts[@]}"; do echo "  ${account%%:*}"; done
if (( dry_run )); then
  echo 'Dry run only; no accounts were changed.'
  exit 0
fi
if (( ! confirmed )); then
  if [[ ! -t 0 ]]; then echo 'An interactive terminal is required.' >&2; exit 1; fi
  read -r -p 'Type DELETE VPN USERS to continue: ' answer
  if [[ $answer != 'DELETE VPN USERS' ]]; then echo 'Cancelled.'; exit 1; fi
fi

# Lock every account before ending sessions, so none can reconnect while cleanup runs.
for account in "${accounts[@]}"; do
  usermod --lock "${account%%:*}"
done
for account in "${accounts[@]}"; do
  username=${account%%:*}
  uid=${account#*:}
  if command -v loginctl >/dev/null 2>&1; then
    loginctl terminate-user "$username" 2>/dev/null || true
  fi
  pkill -KILL -u "$uid" 2>/dev/null || true
  pkill -KILL -U "$uid" 2>/dev/null || true
  pkill -KILL -f "^sshd: ${username} \\[priv\\]" 2>/dev/null || true
  for attempt in 1 2 3 4 5; do
    if userdel "$username"; then break; fi
    if (( attempt == 5 )); then
      echo "Could not delete $username. The account remains locked; inspect its processes." >&2
      exit 1
    fi
    sleep 1
    pkill -KILL -u "$uid" 2>/dev/null || true
    pkill -KILL -U "$uid" 2>/dev/null || true
  done
  if getent passwd "$username" >/dev/null; then
    echo "Account $username still exists; stopping cleanup." >&2
    exit 1
  fi
done
if (( ! keep_group )); then groupdel sshvpn; fi
echo 'Panel VPN accounts were removed.'
