import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("sshvpn_web", Path(__file__).resolve().parents[1] / "sshvpn_web.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class WebAddressTests(unittest.TestCase):
    def test_validates_path_and_port(self):
        self.assertEqual(web.web_path("/private-42/"), "/private-42")
        self.assertEqual(web.web_path("/"), "")
        for bad in ("/two/parts", "/../../etc", "/a b", "/a;return 200"):
            with self.assertRaises(ValueError):
                web.web_path(bad)
        for bad in (0, 22, 443, 1023, 65536, "8080", True):
            with self.assertRaises(ValueError):
                web.web_port(bad)
        self.assertEqual(web.web_port(80), 80)
        self.assertEqual(web.web_port(18080), 18080)
        self.assertEqual(web.web_port(443, tls=True), 443)
        with self.assertRaises(ValueError):
            web.web_port(80, tls=True)

    def test_nginx_config_routes_only_under_selected_path(self):
        config = web.nginx_config("65.20.109.66", 18080, "/private-42")
        self.assertIn("listen 18080;", config)
        self.assertIn("location /private-42/static/", config)
        self.assertIn("location = /private-42/login/", config)
        self.assertIn("location /private-42/", config)
        self.assertIn("location / { return 404; }", config)
        self.assertIn("proxy_pass http://127.0.0.1:8000/;", config)
        self.assertNotIn("location = /login/", config)

    def test_tls_config_preserves_certificate_and_http_challenge_port(self):
        old_site = """server {
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}
"""
        directives = web._ssl_directives(old_site)
        config = web.nginx_config("example.com", 18443, "/private-42", tls=True,
                                  ssl_directives=directives)
        self.assertIn("listen 18443 ssl;", config)
        self.assertIn("listen 80;", config)
        self.assertIn("https://$host:18443$request_uri", config)
        self.assertIn("ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;", config)
        self.assertIn("location /private-42/", config)

    def test_stage_preserves_active_config_until_background_apply(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "panel.env"
            site_file = root / "sshvpn-panel"
            state_dir = root / "pending"
            env_file.write_text("PANEL_DOMAIN=example.com\nPANEL_TLS_ENABLED=0\nPANEL_HTTP_PORT=80\nDB_PASSWORD=test\n")
            site_file.write_text("server {\n    listen 80;\n    server_name example.com;\n}\n")
            with patch.object(web, "ENV_FILE", env_file), patch.object(web, "SITE_FILE", site_file), \
                 patch.object(web, "STATE_DIR", state_dir), patch.object(web, "STATE_FILE", state_dir / "state.json"), \
                 patch.object(web, "_port_available", return_value=True), patch.object(web, "_command") as command:
                result = web.stage_change("/private-42", 18080)
                self.assertEqual(result["url"], "http://example.com:18080/private-42/")
                self.assertIn("PANEL_HTTP_PORT=80", env_file.read_text())
                self.assertIn("listen 80;", site_file.read_text())
                self.assertIn("PANEL_WEB_PATH=/private-42", (state_dir / "new.env").read_text())
                self.assertEqual(web.status()["phase"], "scheduled")
                command.assert_called_once()

    def test_failed_activation_restores_previous_address(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "panel.env"
            site_file = root / "sshvpn-panel"
            state_dir = root / "pending"
            old_env = "PANEL_DOMAIN=example.com\nPANEL_TLS_ENABLED=0\nPANEL_HTTP_PORT=80\n"
            old_site = "server { listen 80; server_name example.com; }\n"
            env_file.write_text(old_env)
            site_file.write_text(old_site)
            with patch.object(web, "ENV_FILE", env_file), patch.object(web, "SITE_FILE", site_file), \
                 patch.object(web, "STATE_DIR", state_dir), patch.object(web, "STATE_FILE", state_dir / "state.json"), \
                 patch.object(web, "_port_available", return_value=True), patch.object(web, "_command"):
                web.stage_change("/private-42", 18080)
            token = json.loads((state_dir / "state.json").read_text())["token"]
            checks = 0

            def command(*args):
                nonlocal checks
                if args[:2] == ("/usr/sbin/nginx", "-t"):
                    checks += 1
                    if checks == 1:
                        raise RuntimeError("invalid nginx config")

            with patch.object(web, "ENV_FILE", env_file), patch.object(web, "SITE_FILE", site_file), \
                 patch.object(web, "STATE_DIR", state_dir), patch.object(web, "STATE_FILE", state_dir / "state.json"), \
                 patch.object(web, "_write_owned", side_effect=lambda path, content: path.write_text(content)), \
                 patch.object(web, "_port_available", return_value=True), patch.object(web, "_command", side_effect=command):
                with self.assertRaises(RuntimeError):
                    web.apply_change(token)
            self.assertEqual(env_file.read_text(), old_env)
            self.assertEqual(site_file.read_text(), old_site)
            self.assertFalse(state_dir.exists())

    def test_successful_activation_waits_for_confirmation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "panel.env"
            site_file = root / "sshvpn-panel"
            state_dir = root / "pending"
            env_file.write_text("PANEL_DOMAIN=example.com\nPANEL_TLS_ENABLED=0\nPANEL_HTTP_PORT=80\n")
            site_file.write_text("server { listen 80; server_name example.com; }\n")
            with patch.object(web, "ENV_FILE", env_file), patch.object(web, "SITE_FILE", site_file), \
                 patch.object(web, "STATE_DIR", state_dir), patch.object(web, "STATE_FILE", state_dir / "state.json"), \
                 patch.object(web, "_port_available", return_value=True), patch.object(web, "_command"):
                web.stage_change("/private-42", 18080)
            token = json.loads((state_dir / "state.json").read_text())["token"]
            class Connection:
                def request(self, *args, **kwargs):
                    pass

                def getresponse(self):
                    return type("Response", (), {"status": 200})()

                def close(self):
                    pass

            with patch.object(web, "ENV_FILE", env_file), patch.object(web, "SITE_FILE", site_file), \
                 patch.object(web, "STATE_DIR", state_dir), patch.object(web, "STATE_FILE", state_dir / "state.json"), \
                 patch.object(web, "_port_available", return_value=True), \
                 patch.object(web, "_write_owned", side_effect=lambda path, content: path.write_text(content)), \
                 patch.object(web, "_command") as command, \
                 patch.object(web.http.client, "HTTPConnection", return_value=Connection()):
                web.apply_change(token)
                self.assertEqual(web.status()["phase"], "awaiting-confirmation")
                self.assertIn("PANEL_WEB_PATH=/private-42", env_file.read_text())
                self.assertEqual(web.confirm_change()["url"], "http://example.com:18080/private-42/")
                self.assertFalse(state_dir.exists())
                self.assertTrue(any(call.args[0] == "/usr/bin/systemd-run" for call in command.call_args_list))


if __name__ == "__main__":
    unittest.main()
