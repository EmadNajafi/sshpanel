"""Root-owned policy storage and per-connection admission for VPN SSH accounts."""

import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import grp
import re
import tempfile
import time


USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
GROUP = "sshvpn"
POLICY_DIR = Path("/etc/sshvpn/accounts")
LEASE_DIR = Path("/run/sshvpn-policy")


def validate_username(username):
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise ValueError("Invalid Linux login name.")


def policy_path(username):
    validate_username(username)
    return POLICY_DIR / f"{username}.json"


def write_policy(username, expires_at, max_connections, *, valid_days=None, activated_at=None,
                 traffic_limit_bytes=None):
    path = policy_path(username)
    if expires_at is not None and (type(expires_at) is not int or expires_at <= 0):
        raise ValueError("Invalid expiry time.")
    if max_connections is not None and (type(max_connections) is not int or not 1 <= max_connections <= 10000):
        raise ValueError("Invalid connection limit.")
    if valid_days is not None and (type(valid_days) is not int or not 1 <= valid_days <= 36500):
        raise ValueError("Invalid validity period.")
    if traffic_limit_bytes is not None and (type(traffic_limit_bytes) is not int or
                                            not 1 <= traffic_limit_bytes <= 100000 * 1024 ** 3):
        raise ValueError("Invalid traffic limit.")
    if activated_at is not None and (type(activated_at) is not int or activated_at <= 0):
        raise ValueError("Invalid activation time.")
    if valid_days is not None and ((expires_at is None) != (activated_at is None)):
        raise ValueError("Invalid activation policy.")
    POLICY_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".policy-", dir=POLICY_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump({"expires_at": expires_at, "max_connections": max_connections,
                       "valid_days": valid_days, "activated_at": activated_at,
                       "traffic_limit_bytes": traffic_limit_bytes}, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_policy(username):
    with policy_path(username).open(encoding="utf-8") as source:
        policy = json.load(source)
    expires_at = policy["expires_at"]
    max_connections = policy["max_connections"]
    policy.setdefault("valid_days", None)
    policy.setdefault("activated_at", None)
    policy.setdefault("traffic_limit_bytes", None)
    if expires_at is not None and (type(expires_at) is not int or expires_at <= 0):
        raise ValueError("Invalid expiry policy.")
    if max_connections is not None and (type(max_connections) is not int or not 1 <= max_connections <= 10000):
        raise ValueError("Invalid connection policy.")
    valid_days, activated_at = policy["valid_days"], policy["activated_at"]
    traffic_limit_bytes = policy["traffic_limit_bytes"]
    if traffic_limit_bytes is not None and (type(traffic_limit_bytes) is not int or
                                            not 1 <= traffic_limit_bytes <= 100000 * 1024 ** 3):
        raise ValueError("Invalid traffic policy.")
    if valid_days is not None and (type(valid_days) is not int or not 1 <= valid_days <= 36500):
        raise ValueError("Invalid validity policy.")
    if activated_at is not None and (type(activated_at) is not int or activated_at <= 0):
        raise ValueError("Invalid activation policy.")
    if valid_days is not None and ((expires_at is None) != (activated_at is None)):
        raise ValueError("Invalid activation policy.")
    return policy


def managed_account(username):
    try:
        user = pwd.getpwnam(username)
    except KeyError:
        return False
    if user.pw_gid != grp.getgrnam(GROUP).gr_gid:
        return False
    validate_username(username)
    return True


def seed_legacy_accounts():
    group_id = grp.getgrnam(GROUP).gr_gid
    for account in pwd.getpwall():
        if account.pw_gid == group_id and not policy_path(account.pw_name).exists():
            write_policy(account.pw_name, None, None)


def process_start_time(pid):
    # /proc/<pid>/stat field 22, after the parenthesized command field.
    raw = Path(f"/proc/{pid}/stat").read_text()
    return raw[raw.rfind(")") + 2:].split()[19]


def lease_path(username):
    validate_username(username)
    LEASE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return LEASE_DIR / f"{username}.json"


def admit_connection(username, monitor_pid, now=None, remote_ip=None):
    now = time.time() if now is None else now
    path = lease_path(username)
    with path.open("a+", encoding="utf-8") as state:
        os.chmod(path, 0o600)
        fcntl.flock(state, fcntl.LOCK_EX)
        policy = read_policy(username)  # Re-read while holding the account's admission lock.
        if policy["expires_at"] is not None and now >= policy["expires_at"]:
            return False
        if policy["traffic_limit_bytes"] is not None:
            from sshvpn_usage import snapshot
            current = snapshot().get(username, {})
            if current.get("upload_bytes", 0) + current.get("download_bytes", 0) >= policy["traffic_limit_bytes"]:
                return False
        limit = policy["max_connections"]
        start_time = process_start_time(monitor_pid)
        state.seek(0)
        try:
            leases = json.load(state)
        except json.JSONDecodeError:
            leases = []
        live = []
        for lease in leases:
            try:
                if process_start_time(lease["pid"]) == lease["start_time"]:
                    live.append(lease)
            except (FileNotFoundError, ProcessLookupError, KeyError, ValueError):
                continue
        try:
            address = str(ipaddress.ip_address(remote_ip)) if remote_ip else None
        except ValueError:
            address = None
        current = {"pid": monitor_pid, "start_time": start_time, "ip": address}
        existing = next((lease for lease in live if lease["pid"] == monitor_pid and lease["start_time"] == start_time), None)
        if existing is not None:
            if address and not existing.get("ip"):
                existing["ip"] = address
            allowed = True
        elif limit is not None and len(live) >= limit:
            allowed = False
        else:
            if policy["valid_days"] is not None and policy["activated_at"] is None:
                activated_at = int(now)
                expires_at = activated_at + policy["valid_days"] * 86400
                write_policy(username, expires_at, limit,
                             valid_days=policy["valid_days"], activated_at=activated_at,
                             traffic_limit_bytes=policy["traffic_limit_bytes"])
            live.append(current)
            allowed = True
        state.seek(0)
        state.truncate()
        json.dump(live, state)
        state.flush()
        os.fsync(state.fileno())
        return allowed
