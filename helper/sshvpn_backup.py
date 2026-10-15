#!/opt/ssh-vpn-panel/.venv/bin/python
"""Create and restore complete SSH VPN panel backups.

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
from sshvpn_user_import import read_archive as read_user_import_archive, import_users

BACKUP_DIR = Path("/var/lib/sshvpn-panel/backups")
UPLOAD_DIR = Path("/var/lib/sshvpn-panel/restore-uploads")
LOCK = Path("/run/lock/sshvpn-backup.lock")
ENV_FILE = Path("/etc/sshvpn/panel.env")
POLICY_DIR = Path("/etc/sshvpn/accounts")
USAGE_FILE = Path("/var/lib/sshvpn/usage.json")
SCHEDULE_FILE = Path("/etc/sshvpn/backup-schedule.json")
TIMER_FILE = Path("/etc/systemd/system/sshvpn-backup.timer")
CHUNK = 1024 * 1024
USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SAVED_NAME = re.compile(r"^sshpanel-(?:auto-)?\d{8}T\d{6}Z-[0-9a-f]{8}\.tar\.gz$")
UPLOAD_NAME = re.compile(r"^restore-[0-9a-f]{32}\.tar\.gz$")
BACKUP_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
LEGACY_MEMBERS = {"manifest.json", "database.dump", "panel.env", "accounts.json", "usage.json", "nginx.conf", "sshd_config"}
MEMBERS = LEGACY_MEMBERS | {"backup_schedule.json"}
DATA_MEMBERS = MEMBERS - {"manifest.json"}


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


def create(automatic=False):
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
        content = {
            "panel.env": ENV_FILE.read_bytes(),
            "accounts.json": json.dumps(accounts).encode(),
            "usage.json": USAGE_FILE.read_bytes(),
            "nginx.conf": Path("/etc/nginx/sites-available/sshvpn-panel").read_bytes(),
            "sshd_config": Path("/etc/ssh/sshd_config").read_bytes(),
            "backup_schedule.json": json.dumps(schedule_status()).encode(),
        }
        manifest = {
            "format": 4, "created_at": datetime.now(timezone.utc).isoformat(),
            "account_count": len(accounts),
            "sha256": {"database.dump": file_hash(database), **{
                name: hashlib.sha256(data).hexdigest() for name, data in content.items()
            }},
        }
        archive_path = directory / "backup.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            add_bytes(archive, "manifest.json", json.dumps(manifest).encode())
            archive.add(database, arcname="database.dump", recursive=False)
            for member_name, data in content.items():
                add_bytes(archive, member_name, data)
        prefix = "sshpanel-auto-" if automatic else "sshpanel-"
        name = f"{prefix}{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.urandom(4).hex()}.tar.gz"
        pending = BACKUP_DIR / f".{name}.tmp"
        try:
            shutil.copyfile(archive_path, pending)
            os.chown(pending, 0, grp.getgrnam("sshvpn-panel").gr_gid)
            os.chmod(pending, 0o640)
            os.replace(pending, BACKUP_DIR / name)
        finally:
            pending.unlink(missing_ok=True)
    return name


def read_archive(path, directory):
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        member_names = {member.name for member in members}
        if member_names not in (MEMBERS, LEGACY_MEMBERS) or len(members) != len(member_names) or any(not member.isfile() for member in members):
            raise ValueError("Backup contents are incomplete or unexpected.")
        if sum(member.size for member in members) > 1024 * 1024 * 1024:
            raise ValueError("Backup expands beyond the 1 GiB restore limit.")
        for member in members:
            destination = directory / member.name
            with archive.extractfile(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, CHUNK)
    manifest = json.loads((directory / "manifest.json").read_text())
    if not isinstance(manifest, dict) or manifest.get("format") not in (2, 3, 4):
        raise ValueError("Unsupported or incomplete backup.")
    if (manifest["format"] == 4 and member_names != MEMBERS or
            manifest["format"] in (2, 3) and member_names != LEGACY_MEMBERS):
        raise ValueError("Backup format does not match its contents.")
    if manifest["format"] in (3, 4):
        hashes = manifest.get("sha256")
        required = DATA_MEMBERS if manifest["format"] == 4 else LEGACY_MEMBERS - {"manifest.json"}
        if not isinstance(hashes, dict) or set(hashes) != required:
            raise ValueError("Backup checksums are incomplete.")
        for name in required:
            if file_hash(directory / name) != hashes[name]:
                raise ValueError(f"Backup checksum mismatch: {name}.")
    elif file_hash(directory / "database.dump") != manifest.get("database_sha256"):
        raise ValueError("Database checksum mismatch.")
    accounts = json.loads((directory / "accounts.json").read_text())
    source_env = (directory / "panel.env").read_text()
    if not isinstance(accounts, list) or manifest.get("account_count") != len(accounts):
        raise ValueError("Unsupported or incomplete backup.")
    secret = next((line.split("=", 1)[1] for line in source_env.splitlines() if line.startswith("DJANGO_SECRET_KEY=")), None)
    if not secret:
        raise ValueError("The credential encryption key is missing.")
    usage = json.loads((directory / "usage.json").read_text())
    if not isinstance(usage, dict):
        raise ValueError("Traffic data is invalid.")
    seen = set()
    for item in accounts:
        if not isinstance(item, dict):
            raise ValueError("Invalid VPN account in backup.")
        username = item.get("username")
        policy = item.get("policy")
        password_hash = item.get("password_hash")
        if not isinstance(username, str) or not USERNAME.fullmatch(username) or username in seen:
            raise ValueError("Invalid or duplicate VPN account in backup.")
        if not isinstance(password_hash, str) or not password_hash or any(char in password_hash for char in ":\n\r\x00"):
            raise ValueError("Invalid VPN password hash in backup.")
        linux = item.get("linux")
        if manifest["format"] in (3, 4) or linux is not None:
            aging = ("shadow_last_change", "shadow_min", "shadow_max", "shadow_warn",
                     "shadow_inactive", "shadow_expire", "shadow_flag")
            if not isinstance(linux, dict) or not all(
                key in linux and (linux[key] is None or type(linux[key]) is int) for key in aging
            ) or linux.get("home") != "/var/empty" or linux.get("shell") != "/usr/sbin/nologin":
                raise ValueError("Invalid Linux VPN account metadata in backup.")
            if not isinstance(linux.get("gecos"), str) or any(
                character in linux["gecos"] for character in ":\n\r\x00"
            ):
                raise ValueError("Invalid Linux VPN account description in backup.")
        old_keys = {"expires_at", "max_connections"}
        new_keys = old_keys | {"valid_days", "activated_at"}
        traffic_keys = new_keys | {"traffic_limit_bytes"}
        if not isinstance(policy, dict) or set(policy) not in (old_keys, new_keys, traffic_keys):
            raise ValueError("Invalid VPN policy in backup.")
        expiry, limit = policy["expires_at"], policy["max_connections"]
        if expiry is not None and (type(expiry) is not int or expiry <= 0):
            raise ValueError("Invalid VPN expiry in backup.")
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 10000):
            raise ValueError("Invalid VPN connection limit in backup.")
        traffic_limit = policy.get("traffic_limit_bytes")
        if traffic_limit is not None and (type(traffic_limit) is not int or
                                          not 1 <= traffic_limit <= 100000 * 1024 ** 3):
            raise ValueError("Invalid VPN traffic limit in backup.")
        validity, activated = policy.get("valid_days"), policy.get("activated_at")
        if validity is not None and (type(validity) is not int or not 1 <= validity <= 36500):
            raise ValueError("Invalid VPN validity in backup.")
        if activated is not None and (type(activated) is not int or activated <= 0):
            raise ValueError("Invalid VPN activation in backup.")
        if validity is not None and ((expiry is None) != (activated is None)):
            raise ValueError("Invalid VPN activation state in backup.")
        seen.add(username)
    if manifest["format"] in (3, 4):
        if set(usage) != seen or any(
            not isinstance(entry, dict) or any(
                type(entry.get(key)) is not int or entry[key] < 0
                for key in ("up", "down", "last_up", "last_down", "uid")
            ) for entry in usage.values()
        ):
            raise ValueError("Traffic data does not match the VPN accounts.")
    if manifest["format"] == 4:
        schedule = json.loads((directory / "backup_schedule.json").read_text())
        if (not isinstance(schedule, dict) or type(schedule.get("enabled")) is not bool or
                not isinstance(schedule.get("time"), str) or not BACKUP_TIME.fullmatch(schedule["time"])):
            raise ValueError("Backup schedule is invalid.")
    return accounts, secret


def restore_linux_metadata(item):
    """Restore portable shadow aging and account description; UIDs remain local."""
    linux = item.get("linux")
    if not linux:
        return  # Older format-2 archives did not require Linux metadata.
    username = item["username"]
    run("/usr/sbin/usermod", "--comment", linux["gecos"], username)
    for option, field in (("-d", "shadow_last_change"), ("-m", "shadow_min"),
                          ("-M", "shadow_max"), ("-W", "shadow_warn"),
                          ("-I", "shadow_inactive"), ("-E", "shadow_expire")):
        value = linux.get(field)
        if value is not None:
            run("/usr/bin/chage", option, str(value), username)


def source_archive(name):
    if not isinstance(name, str):
        raise ValueError("Invalid backup name.")
    if UPLOAD_NAME.fullmatch(name):
        source = UPLOAD_DIR / name
    elif SAVED_NAME.fullmatch(name):
        source = BACKUP_DIR / name
    else:
        raise ValueError("Invalid backup name.")
    if not source.is_file() or source.is_symlink():
        raise ValueError("Backup file was not found.")
    return source


def remove_saved(name):
    if not isinstance(name, str) or not SAVED_NAME.fullmatch(name):
        raise ValueError("Invalid backup name.")
    source_archive(name).unlink()
    return name


def restore(archive_name):
    upload = source_archive(archive_name)
    values = env_values()
    with tempfile.TemporaryDirectory(prefix="sshpanel-restore-") as temporary:
        directory = Path(temporary)
        accounts, secret = read_archive(upload, directory)
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
            restore_linux_metadata(item)
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
        schedule_file = directory / "backup_schedule.json"
        if schedule_file.exists():
            schedule = json.loads(schedule_file.read_text())
            set_schedule(schedule["enabled"], schedule["time"])
        # The new signing key must be loaded by a fresh Gunicorn process after
        # this HTTP request has returned to the browser.
        run("/usr/bin/systemd-run", "--quiet", "--on-active=5s", "/usr/bin/systemctl", "restart", "sshvpn-panel")
    return len(accounts)


def import_user_archive(archive_name, actor_id):
    upload = source_archive(archive_name)
    if type(actor_id) is not int or actor_id <= 0:
        raise ValueError("Invalid importing administrator.")
    users = read_user_import_archive(upload)
    recovery_name = create()
    return {"accounts": import_users(users, actor_id, env_values()), "recovery_backup": recovery_name}


def restore_archive(name, actor_id):
    source = source_archive(name)
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if len(members) == len(names) and names in (MEMBERS, LEGACY_MEMBERS):
            return {"type": "full", "accounts": restore(name)}
        if len(members) == 2 and names == {"manifest.json", "users.json"}:
            return {"type": "users", **import_user_archive(name, actor_id)}
    raise ValueError("Unsupported backup archive.")


def schedule_status():
    if not SCHEDULE_FILE.exists():
        return {"enabled": False, "time": "03:00", "timezone": "Asia/Tehran"}
    state = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or type(state.get("enabled")) is not bool or
            not isinstance(state.get("time"), str) or not BACKUP_TIME.fullmatch(state["time"])):
        raise ValueError("Automatic backup schedule is invalid.")
    return {"enabled": state["enabled"], "time": state["time"], "timezone": "Asia/Tehran"}


def _atomic_file(path, content, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def set_schedule(enabled, time):
    if type(enabled) is not bool or not isinstance(time, str) or not BACKUP_TIME.fullmatch(time):
        raise ValueError("Choose a valid backup time in HH:MM format.")
    old_state = schedule_status()
    old_timer = TIMER_FILE.read_text(encoding="utf-8") if TIMER_FILE.exists() else None
    old_schedule = SCHEDULE_FILE.read_text(encoding="utf-8") if SCHEDULE_FILE.exists() else None
    timer = ("[Unit]\nDescription=Daily SSH VPN panel backup\n\n[Timer]\n"
             f"OnCalendar=*-*-* {time}:00 Asia/Tehran\nPersistent=true\n"
             "Unit=sshvpn-backup.service\n\n[Install]\nWantedBy=timers.target\n")
    try:
        _atomic_file(TIMER_FILE, timer, 0o644)
        _atomic_file(SCHEDULE_FILE, json.dumps({"enabled": enabled, "time": time}) + "\n", 0o600)
        run("/usr/bin/systemctl", "daemon-reload", timeout=30)
        if enabled:
            run("/usr/bin/systemctl", "enable", "--now", "sshvpn-backup.timer", timeout=30)
            run("/usr/bin/systemctl", "restart", "sshvpn-backup.timer", timeout=30)
        else:
            run("/usr/bin/systemctl", "disable", "--now", "sshvpn-backup.timer", timeout=30)
    except Exception:
        if old_timer is None:
            TIMER_FILE.unlink(missing_ok=True)
        else:
            _atomic_file(TIMER_FILE, old_timer, 0o644)
        if old_schedule is None:
            SCHEDULE_FILE.unlink(missing_ok=True)
        else:
            _atomic_file(SCHEDULE_FILE, old_schedule, 0o600)
        try:
            run("/usr/bin/systemctl", "daemon-reload", timeout=30)
            if old_state["enabled"]:
                run("/usr/bin/systemctl", "enable", "--now", "sshvpn-backup.timer", timeout=30)
            else:
                run("/usr/bin/systemctl", "disable", "--now", "sshvpn-backup.timer", timeout=30)
        except Exception:
            pass
        raise
    return schedule_status()


def main():
    actions = {"create", "create-auto", "restore", "remove", "schedule-status", "schedule-set"}
    if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in actions:
        print("Invalid backup operation.", file=sys.stderr)
        return 1
    try:
        action = sys.argv[1]
        request = {} if action in {"create-auto", "schedule-status"} else json.loads(sys.stdin.read(4097))
        with LOCK.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if action == "create":
                print(json.dumps({"name": create()}))
            elif action == "create-auto":
                print(json.dumps({"name": create(automatic=True)}))
            elif action == "restore":
                print(json.dumps(restore_archive(request["name"], request["actor_id"])))
            elif action == "remove":
                print(json.dumps({"name": remove_saved(request["name"])}))
            elif action == "schedule-status":
                print(json.dumps(schedule_status()))
            else:
                print(json.dumps(set_schedule(request["enabled"], request["time"])))
    except (KeyError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired, tarfile.TarError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
