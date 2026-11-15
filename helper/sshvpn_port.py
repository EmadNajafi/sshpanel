"""Dedicated SSH listener for VPN accounts and its two-step port changes.

VPN accounts log in through their own sshd instance (sshvpn-sshd.service,
/etc/ssh/sshvpn_sshd_config) that only admits the `sshvpn` group. The
administrator's main SSH service and port are never modified here; the panel
can only allow or deny VPN logins on the main port during migration.
"""
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time


CONFIG = Path("/etc/ssh/sshvpn_sshd_config")
STATE = Path("/etc/sshvpn/ssh-port-pending.json")
DENY_FILE = Path("/etc/ssh/sshd_config.d/05-sshvpn-deny.conf")
DENY_CONTENT = "DenyGroups sshvpn\n"
SERVICE = "sshvpn-sshd.service"
GROUP = "sshvpn"
PORT_LINE = re.compile(r"^Port[ \t]+(\d+)[ \t]*$", re.MULTILINE)
SSH_PROCESS = re.compile(r'\("sshd[^"]*",pid=(\d+),')
# Nginx serves the panel and certificate renewal on these ports.
RESERVED_PORTS = {80, 443}
RESTRICTIONS = {
    "maxsessions 0", "allowtcpforwarding local", "permittty no",
    "forcecommand /usr/sbin/nologin", "passwordauthentication yes",
    "pubkeyauthentication no",
}


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"Could not run {args[0]}.")
    return result.stdout


def _active(unit):
    return subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", unit],
                          capture_output=True, timeout=15).returncode == 0


def configured_ports(content=None):
    content = CONFIG.read_text(encoding="utf-8") if content is None else content
    ports = [int(value) for value in PORT_LINE.findall(content)]
    if not ports or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("The VPN SSH configuration has no valid port.")
    return ports


def with_ports(content, ports):
    """Replace every Port line with `ports`, keeping the first line's position."""
    match = PORT_LINE.search(content)
    if match is None:
        raise ValueError("The VPN SSH configuration has no Port line.")
    lines = "\n".join(f"Port {port}" for port in ports)
    tail = re.sub(r"(?m)^Port[ \t]+\d+[ \t]*(?:\n|\Z)", "", content[match.end():])
    return content[:match.start()] + lines + tail


def main_ports():
    ports = []
    for line in command("/usr/sbin/sshd", "-T").splitlines():
        if line.startswith("port "):
            ports.append(int(line.split()[1]))
    return ports


def current_state():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text(encoding="utf-8"))


def current_port():
    """The VPN port shown to users: the old one until a staged change is confirmed."""
    state = current_state()
    ports = configured_ports()
    if state is not None and state.get("old") in ports:
        return state["old"]
    return ports[0]


def _listener_works(port, attempts=1):
    for attempt in range(attempts):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3):
                return True
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(0.5)
    return False


def validate_new_port(port):
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Enter a valid TCP port (1–65535).")
    if port in RESERVED_PORTS:
        raise ValueError("Ports 80 and 443 are reserved for the web panel and certificates.")
    if port in main_ports():
        raise ValueError("This is the administrator SSH port; choose a separate port for VPN users.")
    if _listener_works(port):
        raise ValueError("Another service is already listening on this port.")


def _write_config(content):
    """Atomically replace the VPN sshd configuration and apply it, or roll back."""
    previous = CONFIG.read_text(encoding="utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".sshvpn-sshd-", dir=CONFIG.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.chown(temporary, 0, 0)
        os.replace(temporary, CONFIG)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    try:
        command("/usr/sbin/sshd", "-t", "-f", str(CONFIG))
        _allow_in_firewall(configured_ports(content))
        # SIGHUP makes sshd re-execute and rebind its ports; established
        # tunnels belong to child processes and stay connected.
        command("/usr/bin/systemctl", "reload-or-restart", SERVICE)
    except Exception:
        CONFIG.write_text(previous, encoding="utf-8")
        subprocess.run(["/usr/bin/systemctl", "reload-or-restart", SERVICE], capture_output=True, timeout=15)
        raise


def _allow_in_firewall(ports):
    """Open VPN ports in UFW when it is active; other firewalls are left to the administrator."""
    status = subprocess.run(["/usr/sbin/ufw", "status"], text=True, capture_output=True, timeout=15) \
        if Path("/usr/sbin/ufw").exists() else None
    if status is None or status.returncode or "Status: active" not in status.stdout:
        return
    for port in ports:
        command("/usr/sbin/ufw", "allow", f"{port}/tcp")


def stage_port(port):
    if current_state() is not None:
        raise ValueError("Confirm or cancel the pending port change first.")
    old = current_port()
    if port == old:
        raise ValueError("The new port matches the current VPN port.")
    validate_new_port(port)
    content = CONFIG.read_text(encoding="utf-8")
    STATE.write_text(json.dumps({"old": old, "new": port}), encoding="utf-8")
    STATE.chmod(0o600)
    try:
        _write_config(with_ports(content, [old, port]))
        if not _listener_works(port, attempts=10):
            raise ValueError("The new VPN port did not start listening.")
    except Exception:
        try:
            _write_config(with_ports(content, [old]))
        finally:
            STATE.unlink(missing_ok=True)
        raise
    return {"old": old, "new": port}


def finalize_port():
    state = current_state()
    if state is None:
        raise ValueError("There is no pending port change.")
    new = state["new"]
    if not _listener_works(new, attempts=3):
        raise ValueError("The new VPN port is not listening.")
    staged = CONFIG.read_text(encoding="utf-8")
    try:
        _write_config(with_ports(staged, [new]))
        if not _listener_works(new, attempts=10):
            raise ValueError("The new VPN port could not be verified after closing the old port.")
    except Exception:
        _write_config(staged)
        raise
    STATE.unlink()
    return state


def cancel_port():
    state = current_state()
    if state is None:
        raise ValueError("There is no pending port change.")
    _write_config(with_ports(CONFIG.read_text(encoding="utf-8"), [state["old"]]))
    STATE.unlink()
    return state


def verify_vpn_restrictions(username):
    """Refuse to create accounts unless both SSH services restrict them to forwarding."""
    connection = f"user={username},host=localhost,addr=127.0.0.1"
    dedicated = set(command("/usr/sbin/sshd", "-T", "-f", str(CONFIG), "-C", connection).splitlines())
    if not RESTRICTIONS.issubset(dedicated) or f"allowgroups {GROUP}" not in dedicated:
        raise RuntimeError("VPN account restrictions are not active on the dedicated VPN SSH port.")
    main = set(command("/usr/sbin/sshd", "-T", "-C", connection).splitlines())
    denied = any(line.startswith("denygroups ") and GROUP in line.split()[1:] for line in main)
    if not denied and not RESTRICTIONS.issubset(main):
        raise RuntimeError("VPN account restrictions are not active on the main SSH port.")


def main_access_allowed():
    return not DENY_FILE.exists()


def _reload_main_ssh():
    command("/usr/sbin/sshd", "-t")
    # With socket activation an idle ssh.service reads the file on its next start.
    if _active("ssh.service"):
        command("/usr/bin/systemctl", "reload", "ssh.service")


def set_main_access(allowed):
    """Allow or deny VPN logins on the administrator's main SSH port."""
    if type(allowed) is not bool:
        raise ValueError("Invalid main-port access setting.")
    if DENY_FILE.exists() and DENY_FILE.read_text(encoding="utf-8") != DENY_CONTENT:
        raise ValueError(f"{DENY_FILE} was edited by hand; review it before changing VPN access.")
    if not allowed and not _active(SERVICE):
        raise ValueError("Start the dedicated VPN SSH service before closing the main port to VPN users.")
    if allowed == main_access_allowed():
        return {"allowed": allowed, "disconnected": 0}
    if allowed:
        DENY_FILE.unlink()
    else:
        DENY_FILE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        DENY_FILE.write_text(DENY_CONTENT, encoding="utf-8")
        DENY_FILE.chmod(0o644)
    try:
        _reload_main_ssh()
    except Exception:
        if allowed:
            DENY_FILE.write_text(DENY_CONTENT, encoding="utf-8")
            DENY_FILE.chmod(0o644)
        else:
            DENY_FILE.unlink(missing_ok=True)
        subprocess.run(["/usr/bin/systemctl", "reload", "ssh.service"], capture_output=True, timeout=15)
        raise
    disconnected = 0 if allowed else disconnect_main_port_sessions()
    return {"allowed": allowed, "disconnected": disconnected}


def _vpn_uids():
    import grp
    import pwd
    group_id = grp.getgrnam(GROUP).gr_gid
    return {user.pw_uid for user in pwd.getpwall() if user.pw_gid == group_id}


def _process_uid(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[2])
    return None


def disconnect_main_port_sessions():
    """End VPN tunnels that were opened through the main SSH port."""
    uids = _vpn_uids()
    pids = set()
    for port in main_ports():
        result = subprocess.run(
            ["/usr/bin/ss", "-Htnp", "state", "established", "(", "sport", "=", f":{port}", ")"],
            text=True, capture_output=True, timeout=5,
        )
        if result.returncode:
            continue
        for line in result.stdout.splitlines():
            for match in SSH_PROCESS.finditer(line):
                pid = int(match.group(1))
                try:
                    if _process_uid(pid) in uids:
                        pids.add(pid)
                except (OSError, ValueError, IndexError):
                    continue
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return len(pids)


def status():
    return {
        "vpn_port": current_port(),
        "pending": current_state(),
        "main_ports": main_ports(),
        "main_access": main_access_allowed(),
        "service_active": _active(SERVICE),
    }
