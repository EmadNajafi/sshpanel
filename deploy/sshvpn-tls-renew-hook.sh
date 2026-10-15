#!/usr/bin/env bash
set -euo pipefail
[[ -n "${RENEWED_LINEAGE:-}" ]] || exit 0
exec /usr/local/sbin/sshvpn-tls reconcile "$RENEWED_LINEAGE"
