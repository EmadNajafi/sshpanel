import json
import subprocess


class ProvisionError(Exception):
    pass


def call_helper(action, username=None, password=None, *, expires_at=None, valid_days=None, max_connections=None, traffic_limit_bytes=None, enabled=None, port=None, web_path=None, web_port=None):
    payload = {}
    if username is not None:
        payload["username"] = username
    if password is not None:
        payload["password"] = password
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if valid_days is not None:
        payload["valid_days"] = valid_days
    if max_connections is not None:
        payload["max_connections"] = max_connections
    if traffic_limit_bytes is not None:
        payload["traffic_limit_bytes"] = traffic_limit_bytes
    if enabled is not None:
        payload["enabled"] = enabled
    if port is not None:
        payload["port"] = port
    if web_path is not None:
        payload["path"] = web_path
    if web_port is not None:
        payload["port"] = web_port
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/sshvpnctl", action],
            input=json.dumps(payload), text=True, capture_output=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError("The account service is unavailable.") from exc
    if result.returncode != 0:
        raise ProvisionError(result.stderr.strip() or "The account operation failed.")
    return result.stdout.strip()


def get_ssh_port():
    try:
        port = json.loads(call_helper("port-current"))["port"]
        if type(port) is int and 1 <= port <= 65535:
            return port
    except (ProvisionError, ValueError, KeyError, TypeError):
        pass
    from django.conf import settings
    return settings.VPN_SSH_PORT
