"""Connection admission behavior; filesystem locking runs natively on Linux."""

import importlib
import importlib.machinery
import importlib.util
from io import StringIO
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

    def test_unlimited_policy_admits_multiple_connections(self):
        policy.write_policy("alice", 2000, None)
        with patch.object(policy, "process_start_time", side_effect=lambda pid: str(pid)):
            self.assertTrue(policy.admit_connection("alice", 101, now=1000))
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


class UnlimitedConnectionCommandTests(unittest.TestCase):
    def test_create_zero_stores_unlimited_policy(self):
        request = {"username": "alice", "password": "abc", "valid_days": 7, "max_connections": 0}
        with patch.object(ctl.os, "geteuid", return_value=0, create=True), \
             patch.object(ctl.sys, "argv", ["sshvpnctl", "create"]), \
             patch.object(ctl.sys, "stdin", StringIO(json.dumps(request))), \
             patch.object(ctl.grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=1001), create=True), \
             patch.object(ctl.pwd, "getpwnam", side_effect=KeyError, create=True), \
             patch.object(ctl, "run"), patch.object(ctl, "verify_ssh_policy"), \
             patch.object(ctl, "set_password"), patch.object(ctl, "write_policy") as write:
            self.assertEqual(ctl.main(), 0)
        write.assert_called_once_with("alice", None, None, valid_days=7, traffic_limit_bytes=None)

    def test_update_zero_removes_connection_limit(self):
        request = {"username": "alice", "enabled": True, "max_connections": 0}
        current = {"expires_at": 2000000000, "valid_days": None, "activated_at": None,
                   "max_connections": 2, "traffic_limit_bytes": None}
        with patch.object(ctl.os, "geteuid", return_value=0, create=True), \
             patch.object(ctl.sys, "argv", ["sshvpnctl", "update"]), \
             patch.object(ctl.sys, "stdin", StringIO(json.dumps(request))), \
             patch.object(ctl.grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=1001), create=True), \
             patch.object(ctl, "managed_user"), \
             patch.object(ctl, "read_policy", return_value=current), \
             patch.object(ctl, "getspnam", return_value=types.SimpleNamespace(sp_pwdp="$6$hash")), \
             patch.object(ctl, "run"), patch.object(ctl, "write_policy") as write:
            self.assertEqual(ctl.main(), 0)
        write.assert_called_once_with("alice", 2000000000, None, valid_days=None, activated_at=None,
                                      traffic_limit_bytes=None)


class ExpiredAccountUpdateTests(unittest.TestCase):
    """Editing an expired account must work without silently re-enabling it."""

    def run_update(self, request, current, hashes):
        with patch.object(ctl.os, "geteuid", return_value=0, create=True), \
             patch.object(ctl.sys, "argv", ["sshvpnctl", "update"]), \
             patch.object(ctl.sys, "stdin", StringIO(json.dumps(request))), \
             patch.object(ctl.sys, "stderr", StringIO()), \
             patch.object(ctl.grp, "getgrnam", return_value=types.SimpleNamespace(gr_gid=1001), create=True), \
             patch.object(ctl, "managed_user"), \
             patch.object(ctl, "read_policy", return_value=current), \
             patch.object(ctl, "getspnam", side_effect=lambda _: types.SimpleNamespace(sp_pwdp=hashes[0])), \
             patch.object(ctl, "set_password", side_effect=lambda *_: hashes.__setitem__(0, "$6$new")), \
             patch.object(ctl, "set_password_hash"), \
             patch.object(ctl, "run") as run, patch.object(ctl, "write_policy") as write:
            status = ctl.main()
        return status, run, write

    def test_password_change_on_expired_account_keeps_it_locked(self):
        current = {"expires_at": 1000, "valid_days": None, "activated_at": None,
                   "max_connections": 2, "traffic_limit_bytes": None}
        hashes = ["!$6$old"]
        status, run, write = self.run_update(
            {"username": "alice", "enabled": True, "max_connections": 2, "password": "fresh"}, current, hashes)
        self.assertEqual(status, 0)
        write.assert_called_once_with("alice", 1000, 2, valid_days=None, activated_at=None,
                                      traffic_limit_bytes=None)
        commands = [call.args for call in run.call_args_list]
        self.assertIn(("/usr/sbin/usermod", "--lock", "alice"), commands)
        self.assertNotIn(("/usr/sbin/usermod", "--unlock", "alice"), commands)

    def test_new_expiry_in_the_past_is_rejected(self):
        current = {"expires_at": 2000000000, "valid_days": None, "activated_at": None,
                   "max_connections": 2, "traffic_limit_bytes": None}
        status, _, write = self.run_update(
            {"username": "alice", "enabled": True, "max_connections": 2, "expires_at": 1000}, current, ["$6$h"])
        self.assertEqual(status, 1)
        write.assert_not_called()

    def test_renewal_unlocks_enabled_account(self):
        current = {"expires_at": 1000, "valid_days": None, "activated_at": None,
                   "max_connections": 2, "traffic_limit_bytes": None}
        status, run, _ = self.run_update(
            {"username": "alice", "enabled": True, "max_connections": 2, "expires_at": 4000000000},
            current, ["!$6$old"])
        self.assertEqual(status, 0)
        self.assertIn(("/usr/sbin/usermod", "--unlock", "alice"), [call.args for call in run.call_args_list])


class RequestSizeTests(unittest.TestCase):
    def test_port_stage_rejects_oversized_request(self):
        body = json.dumps({"port": 2222, "padding": "x" * 5000})
        with patch.object(ctl.os, "geteuid", return_value=0, create=True), \
             patch.object(ctl.sys, "argv", ["sshvpnctl", "port-stage"]), \
             patch.object(ctl.sys, "stdin", StringIO(body)), \
             patch.object(ctl.sys, "stderr", StringIO()), \
             patch.object(ctl, "stage_port") as stage:
            self.assertEqual(ctl.main(), 1)
        stage.assert_not_called()


class EgressIsolationTests(unittest.TestCase):
    def nft_result(self, table_present, service_active):
        def fake_run(args, **_):
            if args[:2] == ["/usr/sbin/nft", "list"]:
                return types.SimpleNamespace(returncode=0 if table_present else 1)
            return types.SimpleNamespace(returncode=0 if service_active else 3)
        return fake_run

    def test_missing_table_disconnects_and_reloads_rules(self):
        with tempfile.NamedTemporaryFile() as rules, \
             patch.object(ctl, "EGRESS_RULES", Path(rules.name)), \
             patch.object(ctl.subprocess, "run", side_effect=self.nft_result(False, True)), \
             patch.object(ctl, "disconnect_all_accounts") as disconnect, \
             patch.object(ctl, "run") as run:
            ctl.enforce_egress_isolation()
        disconnect.assert_called_once_with()
        run.assert_called_once_with("/usr/sbin/nft", "-f", rules.name)

    def test_stopped_service_stays_stopped(self):
        with patch.object(ctl.subprocess, "run", side_effect=self.nft_result(False, False)), \
             patch.object(ctl, "disconnect_all_accounts") as disconnect, \
             patch.object(ctl, "run") as run:
            ctl.enforce_egress_isolation()
        disconnect.assert_called_once_with()
        run.assert_not_called()

    def test_present_table_changes_nothing(self):
        with patch.object(ctl.subprocess, "run", side_effect=self.nft_result(True, True)), \
             patch.object(ctl, "disconnect_all_accounts") as disconnect:
            ctl.enforce_egress_isolation()
        disconnect.assert_not_called()


class ShadowTests(unittest.TestCase):
    def test_reads_entry_without_spwd(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as shadow:
            shadow.write("root:*:19000:0:99999:7:::\n")
            shadow.write("alice:!$6$abc:19500:0:99999:7::20000:\n")
        self.addCleanup(Path(shadow.name).unlink)
        with patch.object(policy, "SHADOW", Path(shadow.name)):
            entry = policy.getspnam("alice")
            self.assertEqual(entry.sp_pwdp, "!$6$abc")
            self.assertEqual((entry.sp_lstchg, entry.sp_inact, entry.sp_expire, entry.sp_flag),
                             (19500, -1, 20000, -1))
            with self.assertRaises(KeyError):
                policy.getspnam("bob")

    def test_password_hash_goes_through_stdin(self):
        with patch.object(policy.subprocess, "run",
                          return_value=types.SimpleNamespace(returncode=0)) as run:
            policy.set_password_hash("alice", "$6$salt$hash")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/sbin/chpasswd", "--encrypted"])
        self.assertEqual(kwargs["input"], "alice:$6$salt$hash\n")
        with self.assertRaises(ValueError):
            policy.set_password_hash("alice", "bad:hash")


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
