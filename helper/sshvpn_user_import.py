"""Import a validated users-only archive without replacing panel configuration."""

import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tarfile
from datetime import datetime, timezone

from sshvpn_policy import write_policy, policy_path


USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
FORMAT = "sshpanel-user-import-v1"
MAX_ARCHIVE = 10 * 1024 * 1024
MAX_USERS = 10000


def command(*args, input_data=None):
    result = subprocess.run(args, input=input_data, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout


def read_archive(path):
    if path.stat().st_size > MAX_ARCHIVE:
        raise ValueError("User import file exceeds 10 MiB.")
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if {member.name for member in members} != {"manifest.json", "users.json"} or \
           len(members) != 2 or any(not member.isfile() or member.size > MAX_ARCHIVE for member in members):
            raise ValueError("This is not a users-only import archive.")
        manifest = json.loads(archive.extractfile("manifest.json").read())
        payload = archive.extractfile("users.json").read()
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or \
       manifest.get("users_sha256") != hashlib.sha256(payload).hexdigest():
        raise ValueError("User import checksum or format is invalid.")
    users = json.loads(payload)
    if not isinstance(users, list) or not 1 <= len(users) <= MAX_USERS or manifest.get("account_count") != len(users):
        raise ValueError("User import count is invalid.")
    required = {"username", "password", "max_connections", "expires_at", "valid_days",
                "created_at", "enabled", "traffic_limit_bytes", "referral_note"}
    seen = set()
    for item in users:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("User import row is incomplete.")
        name, password = item["username"], item["password"]
        if not isinstance(name, str) or not USERNAME.fullmatch(name) or name in seen:
            raise ValueError("User import has an invalid or duplicate username.")
        if not isinstance(password, str) or not 1 <= len(password) <= 256 or any(c in password for c in "\r\n\x00"):
            raise ValueError(f"Invalid password for {name}.")
        if type(item["max_connections"]) is not int or not 0 <= item["max_connections"] <= 10000:
            raise ValueError(f"Invalid connection limit for {name}.")
        for field in ("expires_at", "created_at"):
            value = item[field]
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError(f"Invalid {field} for {name}.")
        days = item["valid_days"]
        if days is not None and (type(days) is not int or not 1 <= days <= 36500):
            raise ValueError(f"Invalid validity for {name}.")
        if days is not None and item["expires_at"] is not None:
            raise ValueError(f"Conflicting expiry rules for {name}.")
        if type(item["enabled"]) is not bool:
            raise ValueError(f"Invalid status for {name}.")
        traffic = item["traffic_limit_bytes"]
        if traffic is not None and (type(traffic) is not int or not 1 <= traffic <= 100000 * 1024 ** 3):
            raise ValueError(f"Invalid traffic limit for {name}.")
        referral = item["referral_note"]
        if not isinstance(referral, str) or "\x00" in referral or len(referral) > 10000:
            raise ValueError(f"Invalid referral for {name}.")
        seen.add(name)
    return users


def verify_ssh_restrictions(username):
    result = command("/usr/sbin/sshd", "-T", "-C", f"user={username},host=localhost,addr=127.0.0.1")
    lines = set(result.decode().splitlines())
    required = {"maxsessions 0", "allowtcpforwarding local", "permittty no",
                "forcecommand /usr/sbin/nologin", "passwordauthentication yes", "pubkeyauthentication no"}
    if not required.issubset(lines):
        raise RuntimeError("VPN SSH restrictions are not active; import stopped.")


def import_users(users, actor_id, env_values):
    for key, value in env_values.items():
        os.environ[key] = value
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sshvpn.settings")
    sys.path.insert(0, "/opt/ssh-vpn-panel")
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from accounts.models import VpnAccount
    from accounts.secrets import encrypt_password

    actor = get_user_model().objects.filter(pk=actor_id, is_staff=True, is_active=True).first()
    if actor is None:
        raise ValueError("The importing administrator is no longer active.")
    names = {item["username"] for item in users}
    existing = set(VpnAccount.objects.filter(username__in=names).values_list("username", flat=True))
    if existing:
        raise ValueError(f"User import has {len(existing)} username conflicts with this panel; nothing was imported.")
    for username in names:
        try:
            pwd.getpwnam(username)
        except KeyError:
            continue
        raise ValueError(f"Linux account {username} already exists; nothing was imported.")

    created = []
    to_unlock = []
    try:
        with transaction.atomic():
            for item in users:
                username = item["username"]
                command("/usr/sbin/useradd", "--no-create-home", "--home-dir", "/var/empty",
                        "--shell", "/usr/sbin/nologin", "--gid", "sshvpn", username)
                created.append(username)
                verify_ssh_restrictions(username)
                hashed = command("/usr/bin/openssl", "passwd", "-6", "-stdin",
                                 input_data=(item["password"] + "\n").encode()).decode().strip()
                if not hashed.startswith("$6$"):
                    raise RuntimeError("Password hashing failed.")
                command("/usr/sbin/usermod", "--password", "!" + hashed, username)
                write_policy(username, item["expires_at"], item["max_connections"] or None,
                             valid_days=item["valid_days"], traffic_limit_bytes=item["traffic_limit_bytes"])
                if item["enabled"] and (item["expires_at"] is None or
                                        item["expires_at"] > int(datetime.now(timezone.utc).timestamp())):
                    to_unlock.append(username)
                record = VpnAccount.objects.create(
                    username=username, created_by=actor, enabled=item["enabled"],
                    expires_at=(datetime.fromtimestamp(item["expires_at"], timezone.utc)
                                if item["expires_at"] is not None else None),
                    valid_days=item["valid_days"], max_connections=item["max_connections"] or None,
                    traffic_limit_bytes=item["traffic_limit_bytes"], referral_note=item["referral_note"],
                    password_ciphertext=encrypt_password(item["password"]),
                )
                if item["created_at"] is not None:
                    VpnAccount.objects.filter(pk=record.pk).update(
                        created_at=datetime.fromtimestamp(item["created_at"], timezone.utc))
            for username in to_unlock:
                command("/usr/sbin/usermod", "--unlock", username)
    except Exception:
        failures = []
        for username in reversed(created):
            try:
                command("/usr/sbin/userdel", username)
                policy_path(username).unlink(missing_ok=True)
            except Exception:
                failures.append(username)
        if failures:
            raise RuntimeError(f"Import failed; Linux account cleanup needs manual review for: {', '.join(failures)}")
        raise
    try:
        command("/usr/local/sbin/sshvpnctl", "usage-sync")
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass  # The periodic usage timer retries. Imported accounts have no traffic cap.
    return len(users)
