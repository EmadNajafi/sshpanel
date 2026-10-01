"""Two-step SSH port changes that retain the old listener until confirmed."""
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess


CONFIG = Path("/etc/ssh/sshd_config")
STATE = Path("/etc/sshvpn/ssh-port-pending.json")
BACKUP = Path("/etc/sshvpn/sshd_config_before_port_change")
PORT_LINE = re.compile(r"^\s*Port\s+(\d+)\s*(?:#.*)?$", re.IGNORECASE | re.MULTILINE)
MATCH_LINE = re.compile(r"^\s*Match\s+", re.IGNORECASE | re.MULTILINE)


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Could not run {args[0]}.")
    return result.stdout


def effective_ports():
    ports = []
    for line in command("/usr/sbin/sshd", "-T").splitlines():
        if line.startswith("port "):
            ports.append(int(line.split()[1]))
    if not ports:
        raise ValueError("Could not find the current SSH port.")
    return ports


def current_state():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text(encoding="utf-8"))


def _write_config(content):
    CONFIG.write_text(content, encoding="utf-8")
    command("/usr/sbin/sshd", "-t")
    command("/usr/bin/systemctl", "reload", "ssh")


def _listener_works(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            return True
    except OSError:
        return False


def _add_global_port(content, port):
    line = f"Port {port}\n"
    match = MATCH_LINE.search(content)
    if match:
        return content[:match.start()] + line + content[match.start():]
    return content.rstrip() + "\n" + line


def stage_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Enter a valid TCP port (1–65535).")
    if current_state() is not None:
        raise ValueError("Confirm or cancel the pending port change first.")
    ports = effective_ports()
    if len(ports) != 1:
        raise ValueError("Multiple SSH ports are already configured; change them manually.")
    old = ports[0]
    if port == old:
        raise ValueError("The new port matches the current port.")
    if _listener_works(port):
        raise ValueError("Another service is already listening on the new port.")
    original = CONFIG.read_text(encoding="utf-8")
    matches = PORT_LINE.findall(original)
    if len(matches) > 1 or (matches and int(matches[0]) != old) or (not matches and old != 22):
        raise ValueError("SSH port is configured outside the standard main setting; change it manually.")
    shutil.copy2(CONFIG, BACKUP)
    staged = original
    if not matches:
        staged = _add_global_port(staged, old)
    staged = _add_global_port(staged, port)
    try:
        STATE.write_text(json.dumps({"old": old, "new": port}), encoding="utf-8")
        STATE.chmod(0o600)
        _write_config(staged)
        if not _listener_works(port):
            raise ValueError("The new SSH port did not start listening.")
    except Exception:
        shutil.copy2(BACKUP, CONFIG)
        command("/usr/bin/systemctl", "reload", "ssh")
        STATE.unlink(missing_ok=True)
        BACKUP.unlink(missing_ok=True)
        raise
    return {"old": old, "new": port}


def finalize_port():
    state = current_state()
    if state is None or not BACKUP.exists():
        raise ValueError("There is no pending port change.")
    new = state["new"]
    if not _listener_works(new):
        raise ValueError("The new SSH port is not listening.")
    staged = CONFIG.read_text(encoding="utf-8")
    original = BACKUP.read_text(encoding="utf-8")
    if PORT_LINE.search(original):
        final = PORT_LINE.sub(f"Port {new}", original, count=1)
    else:
        final = _add_global_port(original, new)
    try:
        _write_config(final)
        if not _listener_works(new) or effective_ports() != [new]:
            raise ValueError("The new SSH port could not be verified after closing the old port.")
    except Exception:
        _write_config(staged)
        raise
    STATE.unlink()
    BACKUP.unlink()
    return state


def cancel_port():
    state = current_state()
    if state is None or not BACKUP.exists():
        raise ValueError("There is no pending port change.")
    _write_config(BACKUP.read_text(encoding="utf-8"))
    STATE.unlink()
    BACKUP.unlink()
    return state
