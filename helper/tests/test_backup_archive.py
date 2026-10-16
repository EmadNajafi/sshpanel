"""Verify the password-free backup archive is complete before restore."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch


if sys.platform == "win32":
    for module in ("fcntl", "grp", "pwd", "spwd"):
        sys.modules.setdefault(module, types.ModuleType(module))
sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location("sshvpn_backup", Path(__file__).parents[1] / "sshvpn_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupArchiveTests(unittest.TestCase):
    def make_archive(self, path, *, database=b"database rows", include_usage=True, accounts=None,
                     format_version=2, corrupt=None):
        accounts = [] if accounts is None else accounts
        manifest = {
            "format": 2,
            "account_count": len(accounts),
            "database_sha256": hashlib.sha256(b"database rows").hexdigest(),
        }
        members = {
            "manifest.json": json.dumps(manifest).encode(),
            "database.dump": database,
            "accounts.json": json.dumps(accounts).encode(),
            "panel.env": b"DJANGO_SECRET_KEY=source-key\nDB_NAME=sshvpn\n",
            "usage.json": json.dumps({item["username"]: {
                "up": 123, "down": 456, "last_up": 10, "last_down": 20, "uid": 1500
            } for item in accounts}).encode(),
            "nginx.conf": b"server {}",
            "sshd_config": b"Port 5656",
        }
        if not include_usage:
            del members["usage.json"]
        if format_version in (4, 5, 6):
            members["backup_schedule.json"] = b'{"enabled": true, "time": "04:15", "timezone": "Asia/Tehran"}'
        if format_version == 5:
            members["site_access.json"] = b'{"global_domains":["example.com"],"users":{}}'
        if format_version in (3, 4, 5, 6):
            manifest = {"format": format_version, "account_count": len(accounts), "sha256": {
                name: hashlib.sha256(data).hexdigest() for name, data in members.items() if name != "manifest.json"
            }}
            members["manifest.json"] = json.dumps(manifest).encode()
        if corrupt:
            members[corrupt] += b"modified"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members.items():
                backup.add_bytes(archive, name, data)

    def test_complete_archive_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "backup.tar.gz"
            self.make_archive(path)
            extracted = directory / "extracted"
            extracted.mkdir()
            accounts, secret = backup.read_archive(path, extracted)
            self.assertEqual(accounts, [])
            self.assertEqual(secret, "source-key")
            self.assertEqual((extracted / "database.dump").read_bytes(), b"database rows")

    def test_missing_or_modified_data_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "backup.tar.gz"
            extracted = directory / "extracted"
            extracted.mkdir()
            self.make_archive(path, include_usage=False)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                backup.read_archive(path, extracted)
            self.make_archive(path, database=b"changed rows")
            with self.assertRaisesRegex(ValueError, "checksum"):
                backup.read_archive(path, extracted)

    def test_archive_preserves_pending_activation_and_traffic_limit(self):
        account = {"username": "alice", "password_hash": "$6$example", "policy": {
            "expires_at": None, "max_connections": 2, "valid_days": 30,
            "activated_at": None, "traffic_limit_bytes": 2 * 1024 ** 3,
        }}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "backup.tar.gz"
            self.make_archive(archive, accounts=[account])
            extracted = directory / "extracted"
            extracted.mkdir()
            restored, _ = backup.read_archive(archive, extracted)
            self.assertEqual(restored[0]["policy"], account["policy"])

    def test_new_archive_checks_every_data_member(self):
        for member in backup.DATA_MEMBERS:
            with self.subTest(member=member), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                path = directory / "backup.tar.gz"
                self.make_archive(path, format_version=6, corrupt=member)
                extracted = directory / "extracted"
                extracted.mkdir()
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    backup.read_archive(path, extracted)

    def test_new_archive_restores_locked_account_and_aging(self):
        linux = {"uid": 1500, "gid": 1600, "gecos": "Customer one", "home": "/var/empty",
                 "shell": "/usr/sbin/nologin", "shadow_last_change": 20000,
                 "shadow_min": 0, "shadow_max": 99999, "shadow_warn": 7,
                 "shadow_inactive": -1, "shadow_expire": -1, "shadow_flag": None}
        account = {"username": "alice", "password_hash": "!$6$example", "linux": linux,
                   "policy": {"expires_at": None, "max_connections": 2,
                              "valid_days": 30, "activated_at": None,
                              "traffic_limit_bytes": 2 * 1024 ** 3}}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "backup.tar.gz"
            self.make_archive(path, accounts=[account], format_version=3)
            extracted = directory / "extracted"
            extracted.mkdir()
            restored, _ = backup.read_archive(path, extracted)
            self.assertEqual(restored[0], account)
            with patch.object(backup, "run") as command:
                backup.restore_linux_metadata(restored[0])
            self.assertEqual(command.call_count, 7)
            self.assertIn(("/usr/bin/chage", "-E", "-1", "alice"),
                          [call.args for call in command.call_args_list])

    def test_new_archive_carries_automatic_backup_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "backup.tar.gz"
            self.make_archive(archive, format_version=4)
            extracted = directory / "extracted"
            extracted.mkdir()
            backup.read_archive(archive, extracted)
            self.assertEqual(json.loads((extracted / "backup_schedule.json").read_text())["time"], "04:15")

    def test_old_website_rules_are_verified_but_not_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "backup.tar.gz"
            self.make_archive(archive, format_version=5)
            extracted = directory / "extracted"
            extracted.mkdir()
            backup.read_archive(archive, extracted)
            self.assertEqual(json.loads((extracted / "site_access.json").read_text())["global_domains"], ["example.com"])
            self.assertNotIn("site_access.json", backup.MEMBERS)

    def test_new_archive_has_no_website_rules(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            archive = directory / "backup.tar.gz"
            self.make_archive(archive, format_version=6)
            extracted = directory / "extracted"
            extracted.mkdir()
            backup.read_archive(archive, extracted)
            self.assertFalse((extracted / "site_access.json").exists())

    def test_main_restore_detects_full_and_users_only_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            name = "restore-" + "a" * 32 + ".tar.gz"
            self.make_archive(directory / name)
            with patch.object(backup, "UPLOAD_DIR", directory), patch.object(backup, "restore", return_value=2) as full:
                self.assertEqual(backup.restore_archive(name, 7), {"type": "full", "accounts": 2})
                full.assert_called_once_with(name)
            with tarfile.open(directory / name, "w:gz") as archive:
                backup.add_bytes(archive, "manifest.json", b"{}")
                backup.add_bytes(archive, "users.json", b"[]")
            with patch.object(backup, "UPLOAD_DIR", directory), patch.object(backup, "import_user_archive", return_value={"accounts": 1, "recovery_backup": "recovery"}) as users:
                self.assertEqual(backup.restore_archive(name, 7), {"type": "users", "accounts": 1, "recovery_backup": "recovery"})
                users.assert_called_once_with(name, 7)

    def test_saved_backup_name_validation_and_removal(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            name = "sshpanel-auto-20261014T000000Z-12345678.tar.gz"
            (directory / name).write_bytes(b"archive")
            with patch.object(backup, "BACKUP_DIR", directory):
                self.assertEqual(backup.source_archive(name), directory / name)
                with self.assertRaisesRegex(ValueError, "Invalid backup name"):
                    backup.remove_saved("../outside.tar.gz")
                self.assertEqual(backup.remove_saved(name), name)
                self.assertFalse((directory / name).exists())

    def test_schedule_uses_tehran_time_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(backup, "SCHEDULE_FILE", directory / "schedule.json"), \
                 patch.object(backup, "TIMER_FILE", directory / "backup.timer"), \
                 patch.object(backup, "run") as command:
                self.assertEqual(backup.set_schedule(True, "04:15")["time"], "04:15")
                self.assertIn("OnCalendar=*-*-* 04:15:00 Asia/Tehran", (directory / "backup.timer").read_text())
                self.assertEqual(backup.schedule_status()["enabled"], True)
                self.assertEqual(backup.set_schedule(False, "05:30")["enabled"], False)
                self.assertIn("disable", [call.args[1] for call in command.call_args_list])
                with self.assertRaisesRegex(ValueError, "valid backup time"):
                    backup.set_schedule(True, "25:00")


if __name__ == "__main__":
    unittest.main()
