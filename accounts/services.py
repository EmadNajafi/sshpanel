import json
import subprocess


class ProvisionError(Exception):
    pass


def call_helper(action, username, password=None):
    payload = {"username": username}
    if password is not None:
        payload["password"] = password
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
