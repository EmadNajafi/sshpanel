"""Website access rule validation and SSH configuration rendering."""

import importlib
from pathlib import Path
import sys
import types
import unittest

for module_name in ("fcntl", "grp", "pwd"):
    try:
        importlib.import_module(module_name)
    except ImportError:
        sys.modules[module_name] = types.ModuleType(module_name)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sshvpn_sites as sites  # noqa: E402


SSH = """Port 5656
# sshvpn-panel shared-port policy; keep this block at the end of sshd_config
Match Group sshvpn
    PasswordAuthentication yes
    AllowTcpForwarding local
    MaxSessions 0
"""


class SitePolicyTests(unittest.TestCase):
    def test_global_and_user_rules_replace_only_the_managed_block(self):
        policy = {"global_domains": ["example.com"], "users": {"alice": ["other.example"]}}
        rendered = sites.render_ssh_config(SSH, policy)
        self.assertIn("Match User alice\n    PermitOpen other.example:80 other.example:443", rendered)
        self.assertLess(rendered.index("Match User alice"), rendered.index("Match Group sshvpn"))
        self.assertIn("Match Group sshvpn\n    # sshvpn-site-access global\n    PermitOpen example.com:80 example.com:443", rendered)
        self.assertEqual(sites.render_ssh_config(rendered, policy), rendered)
        self.assertEqual(sites.render_ssh_config(rendered, {"global_domains": [], "users": {}}), SSH)

    def test_only_exact_hostnames_are_accepted(self):
        self.assertEqual(sites.normalize_domains(["Example.COM", "example.com", "WWW.example.com"]),
                         ["example.com", "www.example.com"])
        for value in ("https://example.com", "*.example.com", "8.8.8.8", "example.com:443", "example.com/path", "localhost"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sites.normalize_domains([value])
        with self.assertRaises(ValueError):
            sites.validate_policy({"global_domains": [], "users": {"alice": []}})

    def test_unmanaged_vpn_permitopen_is_preserved_by_refusing_change(self):
        with self.assertRaisesRegex(ValueError, "unmanaged PermitOpen"):
            sites.render_ssh_config(SSH.replace("    MaxSessions", "    PermitOpen any\n    MaxSessions"),
                                    {"global_domains": ["example.com"], "users": {}})


if __name__ == "__main__":
    unittest.main()
