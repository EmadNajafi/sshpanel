import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import types
import unittest


if sys.platform == "win32":
    for module in ("fcntl", "grp", "pwd"):
        sys.modules.setdefault(module, types.ModuleType(module))
sys.path.insert(0, str(Path(__file__).parents[1]))
from sshvpn_user_import import read_archive


class UserImportArchiveTests(unittest.TestCase):
    def make_archive(self, path, *, password="pass123", checksum=True):
        users = [{
            "username": "ali", "password": password, "max_connections": 0,
            "expires_at": 1793491199, "valid_days": None, "created_at": 1790812800,
            "enabled": True, "traffic_limit_bytes": None, "referral_note": "referral text",
        }]
        payload = json.dumps(users).encode()
        manifest = {
            "format": "sshpanel-user-import-v1", "account_count": 1,
            "users_sha256": hashlib.sha256(payload if checksum else b"wrong").hexdigest(),
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, data in (("manifest.json", json.dumps(manifest).encode()), ("users.json", payload)):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def test_valid_archive_preserves_credentials_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.tar.gz"
            self.make_archive(path)
            users = read_archive(path)
            self.assertEqual(users[0]["password"], "pass123")
            self.assertEqual(users[0]["max_connections"], 0)
            self.assertEqual(users[0]["referral_note"], "referral text")

    def test_checksum_or_unsafe_password_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "users.tar.gz"
            self.make_archive(path, checksum=False)
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_archive(path)
            self.make_archive(path, password="unsafe\npassword")
            with self.assertRaisesRegex(ValueError, "password"):
                read_archive(path)


if __name__ == "__main__":
    unittest.main()
