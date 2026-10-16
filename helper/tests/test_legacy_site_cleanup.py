import importlib.util
from pathlib import Path
import unittest


source = Path(__file__).resolve().parents[1] / "sshvpn_legacy_site_cleanup.py"
spec = importlib.util.spec_from_file_location("sshvpn_legacy_site_cleanup", source)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

PLAIN = """Port 5656
# sshvpn-panel shared-port policy; keep this block at the end of sshd_config
Match Group sshvpn
    PasswordAuthentication yes
    MaxSessions 0
"""


class LegacySiteCleanupTests(unittest.TestCase):
    def test_removes_global_and_per_user_rules_without_changing_other_ssh_settings(self):
        old = PLAIN.replace(
            "# sshvpn-panel shared-port policy; keep this block at the end of sshd_config",
            "# sshvpn-site-access begin\nMatch User alice\n    PermitOpen example.com:80 example.com:443\n"
            "# sshvpn-site-access end\n# sshvpn-panel shared-port policy; keep this block at the end of sshd_config",
        ).replace(
            "Match Group sshvpn\n",
            "Match Group sshvpn\n    # sshvpn-site-access global\n"
            "    PermitOpen example.org:80 example.org:443\n",
        )
        self.assertEqual(cleanup.without_website_rules(old), PLAIN)
        self.assertEqual(cleanup.without_website_rules(PLAIN), PLAIN)

    def test_refuses_modified_legacy_block(self):
        changed = PLAIN.replace(
            "# sshvpn-panel shared-port policy; keep this block at the end of sshd_config",
            "# sshvpn-site-access begin\nMatch User alice\n    PermitOpen example.com:443\n"
            "    AllowTcpForwarding yes\n# sshvpn-site-access end\n"
            "# sshvpn-panel shared-port policy; keep this block at the end of sshd_config",
        )
        with self.assertRaisesRegex(ValueError, "unexpected settings"):
            cleanup.without_website_rules(changed)


if __name__ == "__main__":
    unittest.main()
