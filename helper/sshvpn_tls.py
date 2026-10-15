#!/usr/bin/env python3
"""Render the panel's TLS site without letting Certbot choose its listener."""
import os
from pathlib import Path
import sys

from sshvpn_web import ENV_FILE, SITE_FILE, _env, _write_owned, _command, nginx_config, web_path, web_port


CERT_ROOT = Path("/etc/letsencrypt/live")
LETSENCRYPT_DIR = Path("/etc/letsencrypt")


def render(domain, port, path):
    port = web_port(port, tls=True)
    path = web_path(path)
    cert_dir = CERT_ROOT / domain
    certificate = cert_dir / "fullchain.pem"
    private_key = cert_dir / "privkey.pem"
    if not certificate.is_file() or not private_key.is_file():
        raise ValueError(f"Certificate files for {domain} are missing.")
    directives = [f"    ssl_certificate {certificate};",
                  f"    ssl_certificate_key {private_key};"]
    options = LETSENCRYPT_DIR / "options-ssl-nginx.conf"
    dhparams = LETSENCRYPT_DIR / "ssl-dhparams.pem"
    if options.is_file():
        directives.append(f"    include {options};")
    if dhparams.is_file():
        directives.append(f"    ssl_dhparam {dhparams};")
    return nginx_config(domain, port, path, tls=True, ssl_directives="\n".join(directives))


def reconcile(renewed_lineage):
    values = _env(ENV_FILE.read_text(encoding="utf-8"))
    if values.get("PANEL_TLS_ENABLED") != "1":
        return False
    domain = values["PANEL_DOMAIN"]
    if Path(renewed_lineage).resolve() != (CERT_ROOT / domain).resolve():
        return False
    candidate = render(domain, int(values.get("PANEL_HTTPS_PORT", "443")),
                       values.get("PANEL_WEB_PATH", ""))
    previous = SITE_FILE.read_text(encoding="utf-8")
    if candidate != previous:
        _write_owned(SITE_FILE, candidate)
    try:
        _command("/usr/sbin/nginx", "-t")
        _command("/usr/bin/systemctl", "reload", "nginx")
    except Exception:
        if candidate != previous:
            _write_owned(SITE_FILE, previous)
            _command("/usr/bin/systemctl", "reload", "nginx")
        raise
    return True


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run as root.")
    if len(sys.argv) == 5 and sys.argv[1] == "render":
        try:
            print(render(sys.argv[2], int(sys.argv[3]), sys.argv[4]), end="")
        except (ValueError, KeyError) as exc:
            raise SystemExit(str(exc)) from exc
    elif len(sys.argv) == 3 and sys.argv[1] == "reconcile":
        reconcile(sys.argv[2])
    else:
        raise SystemExit("Usage: sshvpn-tls render DOMAIN HTTPS_PORT PATH | reconcile RENEWED_LINEAGE")
