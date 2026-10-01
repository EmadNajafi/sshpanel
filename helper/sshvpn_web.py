"""Validated, reversible HTTP address changes for the managed web panel."""
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile


ENV_FILE = Path("/etc/sshvpn/panel.env")
SITE_FILE = Path("/etc/nginx/sites-available/sshvpn-panel")
STATE_DIR = Path("/etc/sshvpn/web-change-pending")
STATE_FILE = STATE_DIR / "state.json"
SLUG = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


def web_path(value):
    if not isinstance(value, str):
        raise ValueError("Enter a valid panel path.")
    value = value.strip()
    if value in ("", "/"):
        return ""
    slug = value.strip("/")
    if not SLUG.fullmatch(slug):
        raise ValueError("Use one path segment with letters, digits, - or _ (up to 64 characters).")
    return f"/{slug}"


def web_port(value, *, tls=False):
    default = 443 if tls else 80
    if type(value) is not int or value != default and not 1024 <= value <= 65535:
        raise ValueError(f"Use port {default} or a TCP port from 1024 to 65535.")
    return value


def _env(content):
    return dict(line.split("=", 1) for line in content.splitlines() if "=" in line and not line.startswith("#"))


def _set_env(content, changes):
    lines = content.splitlines()
    found = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if key in changes:
            lines[index] = f"{key}={changes[key]}"
            found.add(key)
    lines.extend(f"{key}={value}" for key, value in changes.items() if key not in found)
    return "\n".join(lines) + "\n"


def panel_url(domain, port, path, *, local_only=False, tls=False):
    host = "localhost" if local_only else domain
    suffix = "" if port == (443 if tls else 80) else f":{port}"
    return f"{'https' if tls else 'http'}://{host}{suffix}{path}/"


def _ssl_directives(site):
    directives = []
    for name in ("ssl_certificate", "ssl_certificate_key", "include", "ssl_dhparam"):
        if name == "include":
            pattern = r"^\s*include\s+/etc/letsencrypt/[^;]+;"
        else:
            pattern = rf"^\s*{name}\s+[^;]+;"
        directives.extend(match.group().strip() for match in re.finditer(pattern, site, re.MULTILINE))
    if not any(line.startswith("ssl_certificate ") for line in directives) or not any(
        line.startswith("ssl_certificate_key ") for line in directives
    ):
        raise ValueError("The existing HTTPS certificate configuration is incomplete.")
    return "\n".join(f"    {line}" for line in directives)


def nginx_config(domain, port, path, *, local_only=False, tls=False, ssl_directives=""):
    # The domain comes from the installed panel configuration, not the request.
    if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        raise ValueError("The configured panel host is invalid.")
    listen = f"127.0.0.1:{port}" if local_only else str(port)
    listen_suffix = " ssl" if tls else ""
    static = f"{path}/static/"
    login = f"{path}/login/"
    app = f"{path}/"
    prefix_redirect = f"    location = {path} {{ return 308 {app}; }}\n" if path else ""
    config = f"""server {{
    listen {listen}{listen_suffix};
    server_name {domain};
    client_max_body_size 1100m;
    proxy_read_timeout 420s;
{prefix_redirect}    location {static} {{ alias /opt/ssh-vpn-panel/staticfiles/; }}
    location = {login} {{
        limit_req zone=sshvpn_login burst=20 nodelay;
        proxy_pass http://127.0.0.1:8000/login/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    location {app} {{
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
{('    location / { return 404; }' if path else '')}
{ssl_directives if tls else ''}
}}
"""
    if tls:
        redirect_port = "" if port == 443 else f":{port}"
        config += f"""server {{
    listen 80;
    server_name {domain};
    location / {{ return 301 https://$host{redirect_port}$request_uri; }}
}}
"""
    return config


class _LocalHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=35)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Could not run {args[0]}.")
    return result.stdout


def _write_owned(path, content):
    original = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=".sshvpn-web-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, original.st_mode & 0o777)
        os.chown(temporary, original.st_uid, original.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _state():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def _save_state(state):
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    STATE_FILE.chmod(0o600)


def _port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def stage_change(path, port):
    path = web_path(path)
    if _state() is not None:
        raise ValueError("Confirm the pending web address change or wait for rollback first.")
    old_env = ENV_FILE.read_text(encoding="utf-8")
    values = _env(old_env)
    tls = values.get("PANEL_TLS_ENABLED", "0") == "1"
    port = web_port(port, tls=tls)
    current_path = web_path(values.get("PANEL_WEB_PATH", ""))
    port_key = "PANEL_HTTPS_PORT" if tls else "PANEL_HTTP_PORT"
    current_port = int(values.get(port_key, "443" if tls else "80"))
    if path == current_path and port == current_port:
        raise ValueError("The new web address matches the current address.")
    if port != current_port and not _port_available(port):
        raise ValueError("The new port is already in use on this server.")
    domain = values["PANEL_DOMAIN"]
    old_site = SITE_FILE.read_text(encoding="utf-8")
    local_only = not tls and f"listen 127.0.0.1:{current_port};" in old_site
    new_site = nginx_config(domain, port, path, local_only=local_only, tls=tls,
                            ssl_directives=_ssl_directives(old_site) if tls else "")
    new_env = _set_env(old_env, {port_key: str(port), "PANEL_WEB_PATH": path})
    token = secrets.token_hex(8)
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    try:
        for name, content in (("old.env", old_env), ("old.nginx", old_site),
                              ("new.env", new_env), ("new.nginx", new_site)):
            target = STATE_DIR / name
            target.write_text(content, encoding="utf-8")
            target.chmod(0o600)
        state = {"token": token, "phase": "scheduled", "domain": domain,
                 "path": path, "port": port, "local_only": local_only, "tls": tls}
        _save_state(state)
        _command("/usr/bin/systemd-run", f"--unit=sshvpn-web-apply-{token}", "--on-active=3s",
                 "/usr/bin/python3", "/usr/local/sbin/sshvpn_web.py", "apply", token)
    except Exception:
        shutil.rmtree(STATE_DIR)
        raise
    return {"url": panel_url(domain, port, path, local_only=local_only, tls=tls), "phase": "scheduled"}


def status():
    state = _state()
    if state is None:
        return None
    return {key: state[key] for key in ("phase", "path", "port", "tls")}


def _restore():
    _write_owned(ENV_FILE, (STATE_DIR / "old.env").read_text(encoding="utf-8"))
    _write_owned(SITE_FILE, (STATE_DIR / "old.nginx").read_text(encoding="utf-8"))
    _command("/usr/sbin/nginx", "-t")
    _command("/usr/bin/systemctl", "restart", "sshvpn-panel")
    _command("/usr/bin/systemctl", "reload", "nginx")
    shutil.rmtree(STATE_DIR)


def apply_change(token):
    state = _state()
    if state is None or state["token"] != token or state["phase"] != "scheduled":
        return
    try:
        old_values = _env((STATE_DIR / "old.env").read_text(encoding="utf-8"))
        old_port = int(old_values.get("PANEL_HTTPS_PORT" if state["tls"] else "PANEL_HTTP_PORT", "443" if state["tls"] else "80"))
        if not _port_available(state["port"]) and state["port"] != old_port:
            raise ValueError("The new port became unavailable.")
        _write_owned(ENV_FILE, (STATE_DIR / "new.env").read_text(encoding="utf-8"))
        _write_owned(SITE_FILE, (STATE_DIR / "new.nginx").read_text(encoding="utf-8"))
        _command("/usr/sbin/nginx", "-t")
        _command("/usr/bin/systemctl", "restart", "sshvpn-panel")
        _command("/usr/bin/systemctl", "reload", "nginx")
        if state["tls"]:
            connection = _LocalHTTPSConnection(state["domain"], state["port"], timeout=8,
                                               context=ssl.create_default_context())
        else:
            connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=8)
        try:
            connection.request("GET", f'{state["path"]}/login/', headers={"Host": state["domain"]})
            if connection.getresponse().status != 200:
                raise RuntimeError("The new panel login page did not respond.")
        finally:
            connection.close()
        state["phase"] = "awaiting-confirmation"
        _save_state(state)
        _command("/usr/bin/systemd-run", f"--unit=sshvpn-web-rollback-{token}", "--on-active=5m",
                 "/usr/bin/python3", "/usr/local/sbin/sshvpn_web.py", "rollback", token)
    except Exception:
        _restore()
        raise


def confirm_change():
    state = _state()
    if state is None or state["phase"] != "awaiting-confirmation":
        raise ValueError("There is no web address change ready to confirm.")
    values = _env(ENV_FILE.read_text(encoding="utf-8"))
    port_key = "PANEL_HTTPS_PORT" if state["tls"] else "PANEL_HTTP_PORT"
    if values.get("PANEL_WEB_PATH", "") != state["path"] or int(values.get(port_key, "443" if state["tls"] else "80")) != state["port"]:
        raise ValueError("The active web address does not match the pending change.")
    shutil.rmtree(STATE_DIR)
    return {"url": panel_url(state["domain"], state["port"], state["path"],
                              local_only=state["local_only"], tls=state["tls"])}


def rollback_change(token):
    state = _state()
    if state is not None and state["token"] == token:
        _restore()


if __name__ == "__main__":
    if os.geteuid() != 0 or len(sys.argv) != 3 or sys.argv[1] not in {"apply", "rollback"}:
        raise SystemExit("Root and a valid action are required.")
    if sys.argv[1] == "apply":
        apply_change(sys.argv[2])
    else:
        rollback_change(sys.argv[2])
