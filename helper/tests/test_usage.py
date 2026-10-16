"""Active VPN sessions must include transports that lack an old PAM lease."""

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


for module_name in ("fcntl", "pwd", "grp"):
    try:
        importlib.import_module(module_name)
    except ImportError:  # Windows development host
        sys.modules[module_name] = types.ModuleType(module_name)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_usage as usage  # noqa: E402


class ActiveSessionTests(unittest.TestCase):
    def test_established_ssh_transport_belongs_to_managed_user(self):
        output = (
            '0 0 70.34.223.57:5656 198.51.100.25:39668 '
            'users:(("sshd",pid=2662,fd=4),("sshd",pid=2596,fd=4))\n'
            '0 0 70.34.223.57:5656 203.0.113.20:48800 '
            'users:(("sshd",pid=1912,fd=4))\n'
        )
        with patch.object(usage, "_ssh_ports", return_value={5656}), \
             patch.object(usage.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout=output)) as run, \
             patch.object(usage, "_ssh_process_uid", side_effect=lambda pid: {2662: 1001, 2596: 0, 1912: 0}[pid]):
            sessions = usage._active_ssh_transports({"gc5001": 1001})
        self.assertEqual(sessions, {"gc5001": ["198.51.100.25"]})
        self.assertEqual(run.call_args.args[0][-3:], ["=", ":5656", ")"])

    def test_snapshot_counts_unleased_transport_without_double_counting_lease(self):
        for leases in ([], ["198.51.100.25"]):
            state = {"gc5001": {"up": 0, "down": 0, "last_up": 0, "last_down": 0}}
            with self.subTest(leases=leases), \
                 patch.object(usage, "_locked", side_effect=lambda action: action()), \
                 patch.object(usage, "_read_state", return_value=state), \
                 patch.object(usage, "_sync", return_value={"gc5001": 1001}), \
                 patch.object(usage, "table_data", return_value=[]), \
                 patch.object(usage, "_counter_values", return_value={}), \
                 patch.object(usage, "_active_ssh_transports", return_value={"gc5001": ["198.51.100.25"]}), \
                 patch.object(usage, "_live_connections", return_value=leases), \
                 patch.object(usage, "_save_state"):
                result = usage.snapshot()
            self.assertEqual(result["gc5001"]["connections"], 1)
            self.assertEqual(result["gc5001"]["ips"], ["198.51.100.25"])


if __name__ == "__main__":
    unittest.main()
