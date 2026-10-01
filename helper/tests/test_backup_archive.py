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


if sys.platform == "win32":
    for module in ("fcntl", "grp", "pwd", "spwd"):
        sys.modules.setdefault(module, types.ModuleType(module))
spec = importlib.util.spec_from_file_location("sshvpn_backup", Path(__file__).parents[1] / "sshvpn_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupArchiveTests(unittest.TestCase):
    def make_archive(self, path, *, database=b"database rows", include_usage=True):
        manifest = {
            "format": 2,
            "account_count": 0,
            "database_sha256": hashlib.sha256(b"database rows").hexdigest(),
        }
        members = {
            "manifest.json": json.dumps(manifest).encode(),
            "database.dump": database,
            "accounts.json": b"[]",
            "panel.env": b"DJANGO_SECRET_KEY=source-key\nDB_NAME=sshvpn\n",
            "usage.json": b"{}",
            "nginx.conf": b"server {}",
            "sshd_config": b"Port 5656",
        }
        if not include_usage:
            del members["usage.json"]
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


if __name__ == "__main__":
    unittest.main()
