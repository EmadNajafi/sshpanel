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
        if format_version == 3:
            manifest = {"format": 3, "account_count": len(accounts), "sha256": {
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
                self.make_archive(path, format_version=3, corrupt=member)
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


if __name__ == "__main__":
    unittest.main()
