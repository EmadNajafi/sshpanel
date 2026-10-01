"""Unprivileged web-side interface to the root-owned backup utility."""

import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from datetime import datetime, timezone


BACKUP_DIR = Path("/var/lib/sshvpn-panel/backups")
UPLOAD_DIR = Path("/var/lib/sshvpn-panel/restore-uploads")
BACKUP_NAME = re.compile(r"^sshpanel-\d{8}T\d{6}Z-[0-9a-f]{8}\.svpb$")
MAX_UPLOAD_SIZE = 1024 * 1024 * 1024


class BackupError(Exception):
    pass


def list_backups():
    if not BACKUP_DIR.is_dir():
        return []
    result = []
    for path in BACKUP_DIR.iterdir():
        if BACKUP_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
            stat = path.stat()
            result.append({"name": path.name, "size": stat.st_size, "created": datetime.fromtimestamp(stat.st_mtime, timezone.utc)})
    return sorted(result, key=lambda item: item["name"], reverse=True)


def call_backup(action, **fields):
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/sshvpn-backup", action],
            input=json.dumps(fields), text=True, capture_output=True, timeout=360,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError("Backup service is unavailable or timed out.") from exc
    if result.returncode:
        raise BackupError(result.stderr.strip()[:300] or "Backup operation failed.")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise BackupError("Backup service returned an invalid response.") from exc


def save_upload(upload):
    if upload.size > MAX_UPLOAD_SIZE:
        raise BackupError("Backup upload exceeds the 1 GiB limit.")
    UPLOAD_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = f"restore-{uuid.uuid4().hex}.svpb"
    destination_path = UPLOAD_DIR / name
    try:
        with destination_path.open("xb") as destination:
            size = 0
            for block in upload.chunks():
                size += len(block)
                if size > MAX_UPLOAD_SIZE:
                    raise BackupError("Backup upload exceeds the 1 GiB limit.")
                destination.write(block)
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    os.chmod(destination_path, 0o600)
    return name
