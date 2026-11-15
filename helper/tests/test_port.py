"""Dedicated VPN SSH listener: port changes and main-port access."""

import json
import sys
import tempfile
from pathlib import Path
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_port as port  # noqa: E402

TEMPLATE = (Path(__file__).resolve().parents[2] / "deploy" / "sshvpn_sshd_config").read_text()


class PortFileTests(unittest.TestCase):
    def test_template_renders_one_port(self):
        content = TEMPLATE.replace("__VPN_PORT__", "2222")
        self.assertEqual(port.configured_ports(content), [2222])

    def test_with_ports_replaces_all_port_lines_in_place(self):
        content = TEMPLATE.replace("__VPN_PORT__", "2222")
        staged = port.with_ports(content, [2222, 3333])
        self.assertEqual(port.configured_ports(staged), [2222, 3333])
        final = port.with_ports(staged, [3333])
        self.assertEqual(port.configured_ports(final), [3333])
        # Nothing else changes, and repeated edits do not accumulate blank lines.
        self.assertEqual(final, TEMPLATE.replace("__VPN_PORT__", "3333"))

    def test_template_restricts_to_vpn_group_and_forwarding(self):
        lines = set(TEMPLATE.splitlines())
        for expected in ("AllowGroups sshvpn", "PermitRootLogin no", "MaxSessions 0",
                         "AllowTcpForwarding local", "ForceCommand /usr/sbin/nologin",
                         "PasswordAuthentication yes", "PubkeyAuthentication no", "PermitTTY no"):
            self.assertIn(expected, lines)


class PortChangeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.config = root / "sshvpn_sshd_config"
        self.config.write_text(TEMPLATE.replace("__VPN_PORT__", "2222"))
        self.state = root / "pending.json"
        for name, value in (("CONFIG", self.config), ("STATE", self.state)):
            patcher = patch.object(port, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.listening = {2222}
        for name, value in (
            ("_listener_works", lambda number, attempts=1: number in self.listening),
            ("main_ports", lambda: [22]),
            ("command", lambda *args: ""),
            ("_allow_in_firewall", lambda ports: None),
        ):
            patcher = patch.object(port, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        chown = patch.object(port.os, "chown")  # Tests do not run as root.
        chown.start()
        self.addCleanup(chown.stop)

    def test_stage_keeps_old_port_until_confirmed(self):
        def reload(*args):
            self.listening = set(port.configured_ports())
            return ""
        with patch.object(port, "command", side_effect=reload):
            self.assertEqual(port.stage_port(3333), {"old": 2222, "new": 3333})
            self.assertEqual(port.configured_ports(), [2222, 3333])
            self.assertEqual(port.current_port(), 2222)
            port.finalize_port()
        self.assertEqual(port.configured_ports(), [3333])
        self.assertEqual(port.current_port(), 3333)
        self.assertFalse(self.state.exists())

    def test_cancel_restores_old_port(self):
        def reload(*args):
            self.listening = set(port.configured_ports())
            return ""
        with patch.object(port, "command", side_effect=reload):
            port.stage_port(3333)
            port.cancel_port()
        self.assertEqual(port.configured_ports(), [2222])
        self.assertFalse(self.state.exists())

    def test_failed_listener_rolls_back(self):
        with self.assertRaisesRegex(ValueError, "did not start listening"):
            port.stage_port(3333)
        self.assertEqual(port.configured_ports(), [2222])
        self.assertFalse(self.state.exists())

    def test_rejects_admin_reserved_and_busy_ports(self):
        for value, message in ((22, "administrator SSH port"), (443, "reserved"),
                               (0, "valid TCP port"), (2222, "matches the current")):
            with self.assertRaisesRegex(ValueError, message):
                port.stage_port(value)
        self.listening.add(8080)
        with self.assertRaisesRegex(ValueError, "already listening"):
            port.stage_port(8080)
        self.assertEqual(port.configured_ports(), [2222])


class RestrictionTests(unittest.TestCase):
    def outputs(self, dedicated, main):
        def fake(*args):
            return "\n".join(dedicated if "-f" in args else main)
        return fake

    full = sorted(port.RESTRICTIONS)

    def test_dedicated_and_restricted_main_port_pass(self):
        with patch.object(port, "command", side_effect=self.outputs(self.full + ["allowgroups sshvpn"], self.full)):
            port.verify_vpn_restrictions("alice")

    def test_main_port_may_deny_vpn_group_instead(self):
        with patch.object(port, "command",
                          side_effect=self.outputs(self.full + ["allowgroups sshvpn"], ["denygroups sshvpn"])):
            port.verify_vpn_restrictions("alice")

    def test_unrestricted_main_port_fails(self):
        with patch.object(port, "command", side_effect=self.outputs(self.full + ["allowgroups sshvpn"], [])):
            with self.assertRaisesRegex(RuntimeError, "main SSH port"):
                port.verify_vpn_restrictions("alice")

    def test_dedicated_listener_must_limit_group(self):
        with patch.object(port, "command", side_effect=self.outputs(self.full, self.full)):
            with self.assertRaisesRegex(RuntimeError, "dedicated VPN SSH port"):
                port.verify_vpn_restrictions("alice")


class MainAccessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.deny = Path(directory.name) / "sshd_config.d" / "05-sshvpn-deny.conf"
        for name, value in (("DENY_FILE", self.deny), ("_active", lambda unit: True)):
            patcher = patch.object(port, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_close_writes_deny_file_and_disconnects(self):
        with patch.object(port, "_reload_main_ssh") as reload, \
             patch.object(port, "disconnect_main_port_sessions", return_value=3):
            self.assertEqual(port.set_main_access(False), {"allowed": False, "disconnected": 3})
        self.assertEqual(self.deny.read_text(), port.DENY_CONTENT)
        reload.assert_called_once_with()
        with patch.object(port, "_reload_main_ssh"):
            self.assertEqual(port.set_main_access(True), {"allowed": True, "disconnected": 0})
        self.assertFalse(self.deny.exists())

    def test_failed_reload_restores_previous_state(self):
        with patch.object(port, "_reload_main_ssh", side_effect=ValueError("bad config")), \
             patch.object(port.subprocess, "run"):
            with self.assertRaises(ValueError):
                port.set_main_access(False)
        self.assertFalse(self.deny.exists())

    def test_refuses_to_close_without_dedicated_listener(self):
        with patch.object(port, "_active", return_value=False):
            with self.assertRaisesRegex(ValueError, "Start the dedicated VPN SSH service"):
                port.set_main_access(False)
        self.assertFalse(self.deny.exists())

    def test_hand_edited_deny_file_is_left_alone(self):
        self.deny.parent.mkdir(parents=True)
        self.deny.write_text("DenyGroups sshvpn other\n")
        with self.assertRaisesRegex(ValueError, "edited by hand"):
            port.set_main_access(True)
        self.assertTrue(self.deny.exists())

    def test_disconnects_only_vpn_sessions_on_main_port(self):
        ss_output = ('0 0 1.2.3.4:22 5.6.7.8:5000 users:(("sshd",pid=101,fd=4),("sshd",pid=100,fd=4))\n'
                     '0 0 1.2.3.4:22 5.6.7.9:5001 users:(("sshd",pid=201,fd=4))\n')
        uids = {101: 1500, 100: 0, 201: 1000}
        with patch.object(port, "main_ports", return_value=[22]), \
             patch.object(port, "_vpn_uids", return_value={1500}), \
             patch.object(port, "_process_uid", side_effect=lambda pid: uids[pid]), \
             patch.object(port.subprocess, "run",
                          return_value=types.SimpleNamespace(returncode=0, stdout=ss_output)), \
             patch.object(port.os, "kill") as kill:
            self.assertEqual(port.disconnect_main_port_sessions(), 1)
        kill.assert_called_once_with(101, port.signal.SIGKILL)


class StatusTests(unittest.TestCase):
    def test_status_reports_both_services(self):
        with patch.object(port, "current_port", return_value=2222), \
             patch.object(port, "current_state", return_value=None), \
             patch.object(port, "main_ports", return_value=[22]), \
             patch.object(port, "main_access_allowed", return_value=False), \
             patch.object(port, "_active", return_value=True):
            self.assertEqual(json.loads(json.dumps(port.status())), {
                "vpn_port": 2222, "pending": None, "main_ports": [22],
                "main_access": False, "service_active": True,
            })


if __name__ == "__main__":
    unittest.main()
