#!/opt/ssh-vpn-panel/.venv/bin/python
"""Create and restore complete, encrypted SSH VPN panel backups.

This program is root-owned and invoked only through the panel's sudo rule.
The downloaded file contains the entire PostgreSQL database, the application
secret, every managed Linux login hash and policy, and persisted traffic data.
"""

import fcntl
import grp
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import spwd
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


BACKUP_DIR = Path("/var/lib/sshvpn-panel/backups")
UPLOAD_DIR = Path("/var/lib/sshvpn-panel/restore-uploads")
LOCK = Path("/run/lock/sshvpn-backup.lock")
ENV_FILE = Path("/etc/sshvpn/panel.env")
POLICY_DIR = Path("/etc/sshvpn/accounts")
USAGE_FILE = Path("/var/lib/sshvpn/usage.json")
MAGIC = b"SSHPANEL1"
CHUNK = 1024 * 1024
USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
MEMBERS = {"manifest.json", "database.dump", "panel.env", "accounts.json", "usage.json", "nginx.conf", "sshd_config"}


def run(*args, input_data=None, timeout=300):
    result = subprocess.run(args, input=input_data, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr.decode(errors='replace')[:300]}")
    return result.stdout


def env_values():
    result = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            result[key] = value
    if not result.get("DJANGO_SECRET_KEY") or not result.get("DB_NAME"):
        raise ValueError("Panel environment is incomplete.")
    return result


def managed_accounts():
    group_id = grp.getgrnam("sshvpn").gr_gid
    records = []
    for user in pwd.getpwall():
        if user.pw_gid != group_id:
            continue
        if not USERNAME.fullmatch(user.pw_name):
            raise ValueError("An invalid managed Linux login was found.")
        policy_file = POLICY_DIR / f"{user.pw_name}.json"
        if not policy_file.is_file():
            raise ValueError(f"Policy missing for {user.pw_name}.")
        policy = json.loads(policy_file.read_text())
        shadow = spwd.getspnam(user.pw_name)
        records.append({
            "username": user.pw_name,
            "password_hash": shadow.sp_pwdp,
            "linux": {
                "uid": user.pw_uid, "gid": user.pw_gid, "gecos": user.pw_gecos,
                "home": user.pw_dir, "shell": user.pw_shell,
                "shadow_last_change": shadow.sp_lstchg, "shadow_min": shadow.sp_min,
                "shadow_max": shadow.sp_max, "shadow_warn": shadow.sp_warn,
                "shadow_inactive": shadow.sp_inact, "shadow_expire": shadow.sp_expire,
                "shadow_flag": shadow.sp_flag,
            },
            "policy": policy,
        })
    return sorted(records, key=lambda item: item["username"])


def key_for(passphrase, salt):
    if not isinstance(passphrase, str) or len(passphrase) < 16 or len(passphrase) > 1024:
        raise ValueError("Backup passphrase must contain at least 16 characters.")
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def encrypt(source, target, passphrase):
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = Cipher(algorithms.AES(key_for(passphrase, salt)), modes.GCM(nonce)).encryptor()
    with source.open("rb") as plain, target.open("wb") as output:
        output.write(MAGIC + salt + nonce)
        for block in iter(lambda: plain.read(CHUNK), b""):
            output.write(cipher.update(block))
        output.write(cipher.finalize())
        output.write(cipher.tag)


def decrypt(source, target, passphrase):
    with source.open("rb") as encrypted, target.open("wb") as plain:
        header = encrypted.read(len(MAGIC) + 28)
        if len(header) != len(MAGIC) + 28 or not header.startswith(MAGIC):
            raise ValueError("Invalid backup format.")
        encrypted.seek(0, os.SEEK_END)
        size = encrypted.tell()
        if size < len(header) + 16:
            raise ValueError("Incomplete backup file.")
        encrypted.seek(size - 16)
        tag = encrypted.read(16)
        encrypted.seek(len(header))
        remaining = size - len(header) - 16
        cipher = Cipher(algorithms.AES(key_for(passphrase, header[len(MAGIC):len(MAGIC) + 16])), modes.GCM(header[-12:], tag)).decryptor()
        while remaining:
            block = encrypted.read(min(CHUNK, remaining))
            if not block:
                raise ValueError("Incomplete backup file.")
            plain.write(cipher.update(block))
            remaining -= len(block)
        try:
            plain.write(cipher.finalize())
        except InvalidTag as exc:
            raise ValueError("Wrong passphrase or damaged backup file.") from exc


def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def create(passphrase):
    BACKUP_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(BACKUP_DIR, 0, grp.getgrnam("sshvpn-panel").gr_gid)
    os.chmod(BACKUP_DIR, 0o750)
    values = env_values()
    # Flush the live nft counters into the persisted per-user traffic totals.
    run("/usr/local/sbin/sshvpnctl", "usage-snapshot", timeout=30)
    accounts = managed_accounts()
    with tempfile.TemporaryDirectory(prefix="sshpanel-backup-") as temporary:
        directory = Path(temporary)
        database = directory / "database.dump"
        with database.open("wb") as output:
            result = subprocess.run(
                ["/usr/sbin/runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "-O", "-x", values["DB_NAME"]],
                stdout=output, stderr=subprocess.PIPE, timeout=300,
            )
        if result.returncode or database.stat().st_size == 0:
            raise RuntimeError("PostgreSQL backup failed.")
        database_names = set(run(
            "/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-At", "-d", values["DB_NAME"],
            "-c", "SELECT username FROM accounts_vpnaccount", timeout=30,
        ).decode().splitlines())
        if database_names != {item["username"] for item in accounts}:
            raise RuntimeError("Database and Linux VPN accounts changed during backup; retry shortly.")
        manifest = {
            "format": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "account_count": len(accounts), "database_sha256": file_hash(database),
        }
        archive_path = directory / "backup.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            add_bytes(archive, "manifest.json", json.dumps(manifest).encode())
            archive.add(database, arcname="database.dump", recursive=False)
            add_bytes(archive, "panel.env", ENV_FILE.read_bytes())
            add_bytes(archive, "accounts.json", json.dumps(accounts).encode())
            add_bytes(archive, "usage.json", USAGE_FILE.read_bytes() if USAGE_FILE.exists() else b"{}")
            add_bytes(archive, "nginx.conf", Path("/etc/nginx/sites-available/sshvpn-panel").read_bytes())
            add_bytes(archive, "sshd_config", Path("/etc/ssh/sshd_config").read_bytes())
        name = f"sshpanel-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.urandom(4).hex()}.svpb"
        pending = BACKUP_DIR / f".{name}.tmp"
        try:
            encrypt(archive_path, pending, passphrase)
            os.chown(pending, 0, grp.getgrnam("sshvpn-panel").gr_gid)
            os.chmod(pending, 0o640)
            os.replace(pending, BACKUP_DIR / name)
        finally:
            pending.unlink(missing_ok=True)
    return name


def read_archive(path, directory):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) != len(MEMBERS) or {member.name for member in members} != MEMBERS or any(not member.isfile() for member in members):
            raise ValueError("Backup contents are incomplete or unexpected.")
        if sum(member.size for member in members) > 1024 * 1024 * 1024:
            raise ValueError("Backup expands beyond the 1 GiB restore limit.")
        for member in members:
            destination = directory / member.name
            with archive.extractfile(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, CHUNK)
    manifest = json.loads((directory / "manifest.json").read_text())
    accounts = json.loads((directory / "accounts.json").read_text())
    source_env = (directory / "panel.env").read_text()
    if manifest.get("format") != 1 or not isinstance(accounts, list) or manifest.get("account_count") != len(accounts):
        raise ValueError("Unsupported or incomplete backup.")
    if file_hash(directory / "database.dump") != manifest.get("database_sha256"):
        raise ValueError("Database checksum mismatch.")
    secret = next((line.split("=", 1)[1] for line in source_env.splitlines() if line.startswith("DJANGO_SECRET_KEY=")), None)
    if not secret:
        raise ValueError("The credential encryption key is missing.")
    if not isinstance(json.loads((directory / "usage.json").read_text()), dict):
        raise ValueError("Traffic data is invalid.")
    seen = set()
    for item in accounts:
        username = item.get("username")
        policy = item.get("policy")
        password_hash = item.get("password_hash")
        if not isinstance(username, str) or not USERNAME.fullmatch(username) or username in seen:
            raise ValueError("Invalid or duplicate VPN account in backup.")
        if not isinstance(password_hash, str) or not password_hash or any(char in password_hash for char in ":\n\r\x00"):
            raise ValueError("Invalid VPN password hash in backup.")
        if not isinstance(policy, dict) or set(policy) != {"expires_at", "max_connections"}:
            raise ValueError("Invalid VPN policy in backup.")
        expiry, limit = policy["expires_at"], policy["max_connections"]
        if expiry is not None and (type(expiry) is not int or expiry <= 0):
            raise ValueError("Invalid VPN expiry in backup.")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
            raise ValueError("Invalid VPN connection limit in backup.")
        seen.add(username)
    return accounts, secret


def restore(upload_name, passphrase):
    if not isinstance(upload_name, str) or not re.fullmatch(r"restore-[0-9a-f]{32}\.svpb", upload_name):
        raise ValueError("Invalid upload name.")
    upload = UPLOAD_DIR / upload_name
    if not upload.is_file() or upload.is_symlink():
        raise ValueError("Uploaded backup was not found.")
    values = env_values()
    with tempfile.TemporaryDirectory(prefix="sshpanel-restore-") as temporary:
        directory = Path(temporary)
        plain = directory / "backup.tar.gz"
        decrypt(upload, plain, passphrase)
        accounts, secret = read_archive(plain, directory)
        source_names = {item["username"] for item in accounts}
        target_accounts = managed_accounts()
        target_names = {item["username"] for item in target_accounts}
        # Existing non-panel users are never overwritten, and extra VPN users
        # cannot be silently discarded by restoring an older snapshot.
        if target_names - source_names:
            raise ValueError("The destination has VPN accounts absent from this backup. Use a fresh installation.")
        group_id = grp.getgrnam("sshvpn").gr_gid
        for username in source_names - target_names:
            try:
                existing = pwd.getpwnam(username)
            except KeyError:
                continue
            if existing.pw_gid != group_id:
                raise ValueError(f"An unrelated Linux login already uses {username}.")
        run("/usr/bin/pg_restore", "--list", str(directory / "database.dump"))
        os.chown(directory, 0, grp.getgrnam("postgres").gr_gid)
        os.chmod(directory, 0o710)
        os.chown(directory / "database.dump", 0, grp.getgrnam("postgres").gr_gid)
        os.chmod(directory / "database.dump", 0o640)
        staging = f"sshvpn_restore_{os.getpid()}"
        run("/usr/sbin/runuser", "-u", "postgres", "--", "createdb", "-O", values["DB_USER"], staging)
        try:
            run("/usr/sbin/runuser", "-u", "postgres", "--", "pg_restore", "--role", values["DB_USER"], "--single-transaction", "--clean", "--if-exists", "--no-owner", "--no-acl", "-d", staging, str(directory / "database.dump"), timeout=300)
            staged_names = set(run(
                "/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-At", "-d", staging,
                "-c", "SELECT username FROM accounts_vpnaccount", timeout=30,
            ).decode().splitlines())
            admin_count = int(run(
                "/usr/sbin/runuser", "-u", "postgres", "--", "psql", "-At", "-d", staging,
                "-c", "SELECT COUNT(*) FROM auth_user WHERE is_staff AND is_active", timeout=30,
            ).decode().strip())
            if staged_names != source_names or admin_count < 1:
                raise ValueError("Backup database does not match its VPN accounts or has no active administrator.")
        finally:
            run("/usr/sbin/runuser", "-u", "postgres", "--", "dropdb", "--if-exists", staging)

        # Keep a root-only recovery point before changing live data.
        recovery_dir = Path("/var/backups/sshvpn-panel")
        recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        recovery = recovery_dir / f"before-web-restore-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
        recovery.mkdir(mode=0o700)
        shutil.copy2(ENV_FILE, recovery / "panel.env")
        with (recovery / "database.dump").open("wb") as output:
            result = subprocess.run(["/usr/sbin/runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "-O", "-x", values["DB_NAME"]], stdout=output, stderr=subprocess.PIPE, timeout=300)
        if result.returncode:
            raise RuntimeError("Could not create the pre-restore database backup.")
        (recovery / "accounts.json").write_text(json.dumps(target_accounts))
        if USAGE_FILE.exists():
            shutil.copy2(USAGE_FILE, recovery / "usage.json")
        for item in accounts:
            username = item["username"]
            if username not in target_names:
                run("/usr/sbin/useradd", "--no-create-home", "--home-dir", "/var/empty", "--shell", "/usr/sbin/nologin", "--gid", "sshvpn", username)
            run("/usr/sbin/usermod", "--password", item["password_hash"], username)
            policy_path = POLICY_DIR / f"{username}.json"
            temporary_policy = POLICY_DIR / f".{username}.restore"
            temporary_policy.write_text(json.dumps(item["policy"]))
            os.chmod(temporary_policy, 0o600)
            os.replace(temporary_policy, policy_path)

        # Restore the complete PostgreSQL dump, including admin accounts,
        # sessions, audit history and encrypted VPN password fields.
        run("/usr/sbin/runuser", "-u", "postgres", "--", "pg_restore", "--role", values["DB_USER"], "--single-transaction", "--clean", "--if-exists", "--no-owner", "--no-acl", "-d", values["DB_NAME"], str(directory / "database.dump"), timeout=300)
        # Keep historic totals, then start fresh nft counters for the UIDs
        # assigned by this server. Runtime leases/source IPs are not portable.
        usage = json.loads((directory / "usage.json").read_text())
        for username, entry in usage.items():
            if username in source_names:
                entry["uid"] = pwd.getpwnam(username).pw_uid
                entry["last_up"] = 0
                entry["last_down"] = 0
        run("/usr/local/sbin/sshvpnctl", "disconnect-all", timeout=30)
        existing_table = subprocess.run(["/usr/sbin/nft", "list", "table", "inet", "sshvpn_usage"], capture_output=True, timeout=15)
        if existing_table.returncode == 0:
            run("/usr/sbin/nft", "delete", "table", "inet", "sshvpn_usage", timeout=15)
        temporary_usage = USAGE_FILE.with_name(".usage.restore")
        temporary_usage.write_text(json.dumps(usage))
        os.chmod(temporary_usage, 0o600)
        os.replace(temporary_usage, USAGE_FILE)
        lines = ENV_FILE.read_text().splitlines()
        updated = "\n".join(f"DJANGO_SECRET_KEY={secret}" if line.startswith("DJANGO_SECRET_KEY=") else line for line in lines) + "\n"
        temporary_env = ENV_FILE.with_name(".panel.env.restore")
        temporary_env.write_text(updated)
        os.chown(temporary_env, 0, grp.getgrnam("sshvpn-panel").gr_gid)
        os.chmod(temporary_env, 0o640)
        os.replace(temporary_env, ENV_FILE)
        run("/usr/local/sbin/sshvpnctl", "usage-sync", timeout=30)
        # The new signing key must be loaded by a fresh Gunicorn process after
        # this HTTP request has returned to the browser.
        run("/usr/bin/systemd-run", "--quiet", "--on-active=5s", "/usr/bin/systemctl", "restart", "sshvpn-panel")
    return len(accounts)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in {"create", "restore"}:
        print("Invalid backup operation.", file=sys.stderr)
        return 1
    try:
        request = json.loads(sys.stdin.read(4097))
        with LOCK.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if sys.argv[1] == "create":
                print(json.dumps({"name": create(request["passphrase"])}))
            else:
                print(json.dumps({"accounts": restore(request["name"], request["passphrase"])}))
    except (KeyError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, tarfile.TarError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
