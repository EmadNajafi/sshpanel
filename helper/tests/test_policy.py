"""Connection admission behavior; filesystem locking runs natively on Linux."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


for module_name in ("fcntl", "pwd", "grp"):
    try:
        importlib.import_module(module_name)
    except ImportError:  # Windows development host
        sys.modules[module_name] = types.ModuleType(module_name)
        if module_name == "fcntl":
            sys.modules[module_name].LOCK_EX = 2
            sys.modules[module_name].flock = lambda *_: None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_policy as policy  # noqa: E402


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.policy_dir = patch.object(policy, "POLICY_DIR", root / "accounts")
        self.lease_dir = patch.object(policy, "LEASE_DIR", root / "leases")
        self.policy_dir.start()
        self.lease_dir.start()
        self.addCleanup(self.policy_dir.stop)
        self.addCleanup(self.lease_dir.stop)

    def test_connection_limit_and_stale_monitor(self):
        process_times = {101: "one", 102: "two"}
        policy.write_policy("alice", 2000, 1)
        with patch.object(policy, "process_start_time", side_effect=lambda pid: process_times[pid]):
            self.assertTrue(policy.admit_connection("alice", 101, now=1000))
            self.assertTrue(policy.admit_connection("alice", 101, now=1000))
            self.assertFalse(policy.admit_connection("alice", 102, now=1000))
            del process_times[101]
            self.assertTrue(policy.admit_connection("alice", 102, now=1000))

    def test_expired_account_is_denied(self):
        policy.write_policy("alice", 1000, 2)
        self.assertFalse(policy.admit_connection("alice", 101, now=1000))

    def test_records_ip_on_live_lease_without_duplicating_connection(self):
        policy.write_policy("alice", 2000, 2)
        with patch.object(policy, "process_start_time", return_value="one"):
            self.assertTrue(policy.admit_connection("alice", 101, now=1000, remote_ip="198.51.100.25"))
            self.assertTrue(policy.admit_connection("alice", 101, now=1000, remote_ip="198.51.100.25"))
        leases = json.loads(policy.lease_path("alice").read_text())
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0]["ip"], "198.51.100.25")

    def test_invalid_remote_host_does_not_block_vpn_login(self):
        policy.write_policy("alice", 2000, 2)
        with patch.object(policy, "process_start_time", return_value="one"):
            self.assertTrue(policy.admit_connection("alice", 101, now=1000, remote_ip="not-an-ip"))
        leases = json.loads(policy.lease_path("alice").read_text())
        self.assertIsNone(leases[0]["ip"])


if __name__ == "__main__":
    unittest.main()
