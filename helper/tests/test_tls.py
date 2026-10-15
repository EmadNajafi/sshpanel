import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


HELPER_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sshvpn_tls", HELPER_DIR / "sshvpn_tls.py")
import sys
sys.path.insert(0, str(HELPER_DIR))
tls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tls)


class TLSConfigurationTests(unittest.TestCase):
    def test_custom_https_port_and_http_redirect_use_same_port(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cert_dir = root / "live" / "example.com"
            cert_dir.mkdir(parents=True)
            (cert_dir / "fullchain.pem").write_text("certificate")
            (cert_dir / "privkey.pem").write_text("key")
            with patch.object(tls, "CERT_ROOT", root / "live"), \
                 patch.object(tls, "LETSENCRYPT_DIR", root):
                config = tls.render("example.com", 8443, "/secret")
            self.assertIn("listen 8443 ssl;", config)
            self.assertIn("listen 8443 ssl default_server;", config)
            self.assertIn("listen 80;", config)
            self.assertIn("https://$host:8443$request_uri", config)
            self.assertIn("ssl_certificate ", config)
            self.assertNotIn("listen 443 ssl;", config)

    def test_renewal_restores_configured_listener_and_ignores_other_certificates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cert_dir = root / "live" / "example.com"
            cert_dir.mkdir(parents=True)
            (cert_dir / "fullchain.pem").write_text("certificate")
            (cert_dir / "privkey.pem").write_text("key")
            env = root / "panel.env"
            site = root / "site"
            env.write_text("PANEL_TLS_ENABLED=1\nPANEL_DOMAIN=example.com\n"
                           "PANEL_HTTPS_PORT=8443\nPANEL_WEB_PATH=/secret\n")
            site.write_text("certbot changed this site")
            with patch.object(tls, "CERT_ROOT", root / "live"), \
                 patch.object(tls, "LETSENCRYPT_DIR", root), \
                 patch.object(tls, "ENV_FILE", env), patch.object(tls, "SITE_FILE", site), \
                 patch.object(tls, "_write_owned", side_effect=lambda path, content: path.write_text(content)), \
                 patch.object(tls, "_command") as command:
                self.assertFalse(tls.reconcile(str(root / "live" / "other.com")))
                self.assertEqual(site.read_text(), "certbot changed this site")
                self.assertTrue(tls.reconcile(str(cert_dir)))
                self.assertIn("listen 8443 ssl;", site.read_text())
                command.assert_any_call("/usr/sbin/nginx", "-t")
                command.assert_any_call("/usr/bin/systemctl", "reload", "nginx")

    def test_failed_nginx_validation_restores_previous_site(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cert_dir = root / "live" / "example.com"
            cert_dir.mkdir(parents=True)
            (cert_dir / "fullchain.pem").write_text("certificate")
            (cert_dir / "privkey.pem").write_text("key")
            env = root / "panel.env"
            site = root / "site"
            env.write_text("PANEL_TLS_ENABLED=1\nPANEL_DOMAIN=example.com\n"
                           "PANEL_HTTPS_PORT=8443\n")
            site.write_text("previous working site")

            def command(*args):
                if args == ("/usr/sbin/nginx", "-t"):
                    raise RuntimeError("invalid nginx config")

            with patch.object(tls, "CERT_ROOT", root / "live"), \
                 patch.object(tls, "LETSENCRYPT_DIR", root), \
                 patch.object(tls, "ENV_FILE", env), patch.object(tls, "SITE_FILE", site), \
                 patch.object(tls, "_write_owned", side_effect=lambda path, content: path.write_text(content)), \
                 patch.object(tls, "_command", side_effect=command):
                with self.assertRaises(RuntimeError):
                    tls.reconcile(str(cert_dir))
            self.assertEqual(site.read_text(), "previous working site")


if __name__ == "__main__":
    unittest.main()
