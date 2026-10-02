"""Connection admission behavior; filesystem locking runs natively on Linux."""

import importlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


for module_name in ("fcntl", "pwd", "grp", "spwd"):
    try:
        importlib.import_module(module_name)
    except ImportError:  # Windows development host
        sys.modules[module_name] = types.ModuleType(module_name)
        if module_name == "fcntl":
            sys.modules[module_name].LOCK_EX = 2
            sys.modules[module_name].flock = lambda *_: None

if not hasattr(sys.modules["fcntl"], "flock"):
    sys.modules["fcntl"].LOCK_EX = 2
    sys.modules["fcntl"].flock = lambda *_: None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_policy as policy  # noqa: E402
import sshvpn_usage as usage  # noqa: E402
loader = importlib.machinery.SourceFileLoader("sshvpnctl_test", str(Path(__file__).resolve().parents[1] / "sshvpnctl"))
spec = importlib.util.spec_from_loader(loader.name, loader)
ctl = importlib.util.module_from_spec(spec)
loader.exec_module(ctl)


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

    def test_validity_starts_at_first_admitted_connection_only(self):
        policy.write_policy("alice", None, 1, valid_days=30)
        with patch.object(policy, "process_start_time", side_effect=lambda pid: str(pid)):
            self.assertTrue(policy.admit_connection("alice", 101, now=1000))
            self.assertEqual(policy.read_policy("alice")["expires_at"], 1000 + 30 * 86400)
            self.assertEqual(policy.read_policy("alice")["activated_at"], 1000)
            self.assertFalse(policy.admit_connection("alice", 102, now=1001))
            self.assertEqual(policy.read_policy("alice")["activated_at"], 1000)
            self.assertTrue(policy.admit_connection("alice", 101, now=1002))
            self.assertEqual(policy.read_policy("alice")["expires_at"], 1000 + 30 * 86400)

    def test_traffic_quota_denies_new_connection(self):
        policy.write_policy("alice", 2000, 2, traffic_limit_bytes=1024)
        with patch.object(policy, "process_start_time", return_value="one"), \
             patch.object(usage, "snapshot", return_value={"alice": {"upload_bytes": 600, "download_bytes": 424}}):
            self.assertFalse(policy.admit_connection("alice", 101, now=1000))
        self.assertEqual(json.loads(policy.lease_path("alice").read_text() or "[]"), [])

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


class UsageResetTests(unittest.TestCase):
    def test_reset_uses_current_counter_as_new_baseline(self):
        state = {"alice": {"up": 800, "down": 1200, "last_up": 400, "last_down": 600}}
        with patch.object(usage, "_locked", side_effect=lambda action: action()), \
             patch.object(usage, "_read_state", return_value=state), \
             patch.object(usage, "_sync", return_value={"alice": 1001}), \
             patch.object(usage, "table_data", return_value=[]), \
             patch.object(usage, "_counter_values", return_value={"up_alice": 950, "down_alice": 1350}), \
             patch.object(usage, "_save_state") as save:
            usage.reset_account("alice")
        self.assertEqual(state["alice"], {"up": 0, "down": 0, "last_up": 950, "last_down": 1350})
        save.assert_called_once_with(state)


class QuotaSweepTests(unittest.TestCase):
    def test_reached_quota_disconnects_existing_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "alice.json").write_text("{}")
            with patch.object(ctl, "POLICY_DIR", directory), \
                 patch.object(ctl, "managed_user"), \
                 patch.object(ctl.grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=1001), create=True), \
                 patch.object(ctl, "read_policy", return_value={"expires_at": None, "traffic_limit_bytes": 1024}), \
                 patch.object(ctl, "usage_snapshot", return_value={"alice": {"upload_bytes": 600, "download_bytes": 424}}), \
                 patch.object(ctl, "run") as run:
                ctl.sweep_expired_accounts()
            run.assert_called_once_with("/usr/bin/pkill", "-KILL", "-u", "alice", ok=(0, 1))


if __name__ == "__main__":
    unittest.main()
