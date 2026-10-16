"""Root-owned website destination policy for SSH VPN forwarding.

OpenSSH checks PermitOpen before resolving a requested SOCKS hostname. Rules
are confined to the panel's Match blocks; administrator SSH is unchanged.
"""

import fcntl
import grp
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

from sshvpn_policy import GROUP, USERNAME


POLICY_FILE = Path("/etc/sshvpn/site-access.json")
SSH_CONFIG = Path("/etc/ssh/sshd_config")
LOCK_FILE = Path("/run/lock/sshvpn-site-access.lock")
BACKUP_DIR = Path("/var/backups/sshvpn-panel")
SSH_MARKER = "# sshvpn-panel shared-port policy; keep this block at the end of sshd_config"
BEGIN = "# sshvpn-site-access begin"
END = "# sshvpn-site-access end"
DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
MAX_DOMAINS = 40


def normalize_domains(values):
    if not isinstance(values, list) or len(values) > MAX_DOMAINS:
        raise ValueError(f"Enter at most {MAX_DOMAINS} hostnames.")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > 253:
            raise ValueError("Enter hostnames only, one per line.")
        try:
            name = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError("Invalid hostname.") from exc
        try:
            ipaddress.ip_address(name)
        except ValueError:
            pass
        else:
            raise ValueError("IP addresses are not accepted; enter hostnames.")
        if len(name) > 253 or not DOMAIN.fullmatch(name) or any(len(label) > 63 for label in name.split(".")):
            raise ValueError("Use hostnames such as example.com, without https://, paths or wildcards.")
        if name not in result:
            result.append(name)
    return result


def validate_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {"global_domains", "users"}:
        raise ValueError("Invalid website access policy.")
    global_domains = normalize_domains(policy["global_domains"])
    users = policy["users"]
    if not isinstance(users, dict) or len(users) > 10000:
        raise ValueError("Invalid per-user website policies.")
    checked = {}
    for username, domains in users.items():
        if not isinstance(username, str) or not USERNAME.fullmatch(username):
            raise ValueError("Invalid VPN username in website policy.")
        checked[username] = normalize_domains(domains)
        if not checked[username]:
            raise ValueError("A user-specific website list cannot be empty.")
    return {"global_domains": global_domains, "users": checked}


def status():
    if not POLICY_FILE.exists():
        return {"global_domains": [], "users": {}}
    return validate_policy(json.loads(POLICY_FILE.read_text(encoding="utf-8")))


def _destinations(domains):
    return " ".join(f"{name}:{port}" for name in domains for port in (80, 443))


def render_ssh_config(source, policy):
    policy = validate_policy(policy)
    if source.count(SSH_MARKER) != 1:
        raise ValueError("The managed SSH VPN block is missing or duplicated.")
    prefix, managed = source.split(SSH_MARKER, 1)
    if prefix.count(BEGIN) != prefix.count(END) or prefix.count(BEGIN) > 1:
        raise ValueError("The website access block is malformed.")
    if BEGIN in prefix:
        before, generated = prefix.split(BEGIN, 1)
        if not generated.endswith(END + "\n"):
            raise ValueError("The website access block was edited; refusing to overwrite it.")
        prefix = before
    if managed.count("\nMatch Group sshvpn\n") != 1:
        raise ValueError("The managed VPN Match Group block is missing or duplicated.")
    if "\n    # sshvpn-site-access global\n" in managed:
        before, remainder = managed.split("\n    # sshvpn-site-access global\n", 1)
        line, remainder = remainder.split("\n", 1)
        if not line.startswith("    PermitOpen "):
            raise ValueError("The global website access rule was edited.")
        managed = before + "\n" + remainder
    elif re.search(r"(?mi)^\s*PermitOpen\s", managed):
        raise ValueError("An unmanaged PermitOpen rule exists in the VPN block.")

    blocks = []
    for username, domains in sorted(policy["users"].items()):
        blocks.append(f"Match User {username}\n    PermitOpen {_destinations(domains)}\n")
    if blocks:
        prefix = prefix.rstrip("\n") + "\n" + BEGIN + "\n" + "".join(blocks) + END + "\n"
    else:
        prefix = prefix.rstrip("\n") + "\n"
    if policy["global_domains"]:
        managed = managed.replace("\nMatch Group sshvpn\n", "\nMatch Group sshvpn\n    # sshvpn-site-access global\n    PermitOpen " + _destinations(policy["global_domains"]) + "\n", 1)
    return prefix + SSH_MARKER + managed


def _run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr.strip()[:250]}")
    return result.stdout


def _effective(config, username):
    output = _run("/usr/sbin/sshd", "-T", "-f", str(config), "-C", f"user={username},host=localhost,addr=127.0.0.1")
    return dict(line.split(" ", 1) for line in output.splitlines() if " " in line)


def _managed_names():
    group_id = grp.getgrnam(GROUP).gr_gid
    return {user.pw_name for user in pwd.getpwall() if user.pw_gid == group_id}


def apply_policy(policy, *, disconnect=True, updater=None):
    with LOCK_FILE.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old_policy = status()
        if updater is not None:
            policy = updater(old_policy.copy() | {"users": old_policy["users"].copy()})
        policy = validate_policy(policy)
        names = _managed_names()
        if set(policy["users"]) - names:
            raise ValueError("A website rule refers to a VPN user that no longer exists.")
        if old_policy == policy:
            return policy
        old_config = SSH_CONFIG.read_text(encoding="utf-8")
        new_config = render_ssh_config(old_config, policy)
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=SSH_CONFIG.parent, prefix=".sshvpn-sites-", delete=False, encoding="utf-8") as target:
                temp_name = target.name
                target.write(new_config)
            os.chmod(temp_name, 0o600)
            _run("/usr/sbin/sshd", "-t", "-f", temp_name)
            if _effective(SSH_CONFIG, "root") != _effective(temp_name, "root"):
                raise ValueError("Website rules would change administrator SSH access.")
            for username in names:
                expected = policy["users"].get(username, policy["global_domains"])
                actual = _effective(temp_name, username).get("permitopen", "")
                if actual != (_destinations(expected) if expected else "any"):
                    raise ValueError(f"Website rule is ineffective for {username}.")
            BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup = BACKUP_DIR / f"site-access-sshd-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
            shutil.copy2(SSH_CONFIG, backup)
            os.chmod(backup, 0o600)
            try:
                os.replace(temp_name, SSH_CONFIG)
                temp_name = None
                _run("/usr/bin/systemctl", "reload", "ssh")
                temporary_policy = POLICY_FILE.with_name(".site-access.tmp")
                temporary_policy.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
                os.chmod(temporary_policy, 0o600)
                os.replace(temporary_policy, POLICY_FILE)
            except Exception:
                shutil.copy2(backup, SSH_CONFIG)
                _run("/usr/bin/systemctl", "reload", "ssh")
                raise
        finally:
            if temp_name:
                Path(temp_name).unlink(missing_ok=True)
        if disconnect:
            affected = {name for name in names if
                        old_policy["users"].get(name, old_policy["global_domains"]) !=
                        policy["users"].get(name, policy["global_domains"])}
            for username in affected:
                subprocess.run(["/usr/bin/pkill", "-KILL", "-u", username], capture_output=True, timeout=5)
    return policy


def set_global(domains):
    checked = normalize_domains(domains)
    def update(policy):
        policy["global_domains"] = checked
        return policy
    return apply_policy(None, updater=update)


def set_user(username, domains):
    if not isinstance(username, str) or not USERNAME.fullmatch(username):
        raise ValueError("Invalid VPN username.")
    checked = normalize_domains(domains) if domains is not None else None
    if checked == []:
        raise ValueError("Enter at least one hostname or use Inherit global settings.")
    def update(policy):
        if checked is None:
            policy["users"].pop(username, None)
        else:
            policy["users"][username] = checked
        return policy
    return apply_policy(None, updater=update)


def remove_user(username):
    def update(policy):
        policy["users"].pop(username, None)
        return policy
    apply_policy(None, disconnect=False, updater=update)
