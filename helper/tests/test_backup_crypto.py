"""Verify backup encryption detects wrong keys and altered files."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest


if sys.platform == "win32":
    for module in ("fcntl", "grp", "pwd", "spwd"):
        sys.modules.setdefault(module, types.ModuleType(module))
spec = importlib.util.spec_from_file_location("sshvpn_backup", Path(__file__).parents[1] / "sshvpn_backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupEncryptionTests(unittest.TestCase):
    def test_round_trip_and_wrong_passphrase(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain"
            encrypted = Path(directory) / "encrypted"
            restored = Path(directory) / "restored"
            plain.write_bytes(b"complete database and user credentials" * 10000)
            backup.encrypt(plain, encrypted, "correct very long passphrase")
            self.assertNotIn(b"database and user", encrypted.read_bytes())
            backup.decrypt(encrypted, restored, "correct very long passphrase")
            self.assertEqual(restored.read_bytes(), plain.read_bytes())
            with self.assertRaisesRegex(ValueError, "Wrong passphrase"):
                backup.decrypt(encrypted, restored, "another very long passphrase")

    def test_modified_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain"
            encrypted = Path(directory) / "encrypted"
            restored = Path(directory) / "restored"
            plain.write_bytes(b"sensitive records")
            backup.encrypt(plain, encrypted, "correct very long passphrase")
            content = bytearray(encrypted.read_bytes())
            content[-17] ^= 1
            encrypted.write_bytes(content)
            with self.assertRaisesRegex(ValueError, "damaged backup"):
                backup.decrypt(encrypted, restored, "correct very long passphrase")


if __name__ == "__main__":
    unittest.main()
