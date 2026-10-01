import json
import subprocess


class ProvisionError(Exception):
    pass


def call_helper(action, username, password=None, *, expires_at=None, max_connections=None):
    payload = {"username": username}
    if password is not None:
        payload["password"] = password
    if expires_at is not None:
        payload["expires_at"] = expires_at
    if max_connections is not None:
        payload["max_connections"] = max_connections
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
