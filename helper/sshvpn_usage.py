"""Per-account VPN socket counters and active SSH transport leases."""
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import pwd
import grp
import subprocess
import tempfile

from sshvpn_policy import GROUP, USERNAME, lease_path, process_start_time


TABLE = "sshvpn_usage"
STATE_DIR = Path("/var/lib/sshvpn")
STATE = STATE_DIR / "usage.json"
LOCK = STATE_DIR / "usage.lock"


def nft(*args, missing_ok=False):
    result = subprocess.run(["/usr/sbin/nft", *args], text=True, capture_output=True, timeout=15)
    if result.returncode:
        if missing_ok and "No such file or directory" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or "Could not read network counters.")
    return result.stdout


def table_data():
    output = nft("-j", "list", "table", "inet", TABLE, missing_ok=True)
    return json.loads(output)["nftables"] if output is not None else None


def managed_accounts():
    group_id = grp.getgrnam(GROUP).gr_gid
    return {user.pw_name: user.pw_uid for user in pwd.getpwall()
            if user.pw_gid == group_id and USERNAME.fullmatch(user.pw_name)}


def _save_state(state):
    descriptor, temporary = tempfile.mkstemp(prefix=".usage-", dir=STATE_DIR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, STATE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_state():
    if not STATE.exists():
        return {}
    with STATE.open(encoding="utf-8") as source:
        return json.load(source)


def _locked(action):
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as handle:
        os.chmod(LOCK, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        return action()


def _counter_names(username):
    return f"up_{username}", f"down_{username}"


def _mark(uid):
    if not 0 <= uid < 0x1000000:
        raise ValueError("Linux UID is too large for traffic accounting.")
    return 0x53000000 | uid


def _counter_values(data):
    return {item["counter"]["name"]: item["counter"]["bytes"]
            for item in data if "counter" in item}


def _rule_names(data):
    return {part["counter"] for item in data if "rule" in item
            for part in item["rule"]["expr"] if isinstance(part.get("counter"), str)}


def _ensure_table(state):
    data = table_data()
    if data is None:
        nft("add", "table", "inet", TABLE)
        data = table_data()
        for totals in state.values():
            totals["last_up"] = 0
            totals["last_down"] = 0
    chains = {item["chain"]["name"] for item in data if "chain" in item}
    for hook, priority in (("output", "10"), ("input", "0")):
        if hook not in chains:
            nft("add", "chain", "inet", TABLE, hook, "{", "type", "filter", "hook", hook,
                "priority", priority, ";", "policy", "accept", ";", "}")
    return table_data()


def _sync(state):
    data = _ensure_table(state)
    counters = _counter_values(data)
    rules = _rule_names(data)
    accounts = managed_accounts()
    for username in set(state) - set(accounts):
        _remove_account_unlocked(state, username, data)
    for username, uid in accounts.items():
        up, down = _counter_names(username)
        mark = str(_mark(uid))
        if username in state and state[username].get("uid", uid) != uid:
            _remove_account_unlocked(state, username, data)
            for name in (up, down):
                counters.pop(name, None)
                rules.discard(name)
        entry = state.setdefault(username, {"up": 0, "down": 0, "last_up": 0, "last_down": 0})
        entry["uid"] = uid
        for name, direction in ((up, "up"), (down, "down")):
            if name not in counters:
                nft("add", "counter", "inet", TABLE, name)
                entry[f"last_{direction}"] = 0
        if up not in rules:
            nft("add", "rule", "inet", TABLE, "output", "meta", "skuid", str(uid),
                "ct", "mark", "set", mark, "counter", "name", up)
        if down not in rules:
            nft("add", "rule", "inet", TABLE, "input", "ct", "mark", mark,
                "counter", "name", down)
    return accounts


def _remove_account_unlocked(state, username, data):
    if data is not None:
        names = set(_counter_names(username))
        for item in data:
            rule = item.get("rule")
            if rule and any(part.get("counter") in names for part in rule["expr"]):
                nft("delete", "rule", "inet", TABLE, rule["chain"], "handle", str(rule["handle"]))
        counters = _counter_values(data)
        for name in names:
            if name in counters:
                nft("delete", "counter", "inet", TABLE, name)
    state.pop(username, None)


def _live_connections(username):
    path = lease_path(username)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        try:
            leases = json.load(source)
        except json.JSONDecodeError:
            return []
    live = []
    for lease in leases:
        try:
            if process_start_time(lease["pid"]) == lease["start_time"]:
                try:
                    address = str(ipaddress.ip_address(lease.get("ip"))) if lease.get("ip") else None
                except ValueError:
                    address = None
                live.append(address)
        except (FileNotFoundError, ProcessLookupError, KeyError, ValueError, TypeError):
            continue
    return live


def snapshot():
    def collect():
        state = _read_state()
        accounts = _sync(state)
        counters = _counter_values(table_data())
        result = {}
        for username in accounts:
            entry = state[username]
            up, down = _counter_names(username)
            for direction, name in (("up", up), ("down", down)):
                raw = counters.get(name, 0)
                previous = entry[f"last_{direction}"]
                entry[direction] += raw - previous if raw >= previous else raw
                entry[f"last_{direction}"] = raw
            ips = _live_connections(username)
            result[username] = {
                "upload_bytes": entry["up"],
                "download_bytes": entry["down"],
                "connections": len(ips),
                "ips": ips,
            }
        _save_state(state)
        return result
    return _locked(collect)


def reset_account(username):
    """Start a new accounting period without disturbing live VPN sessions."""
    if not USERNAME.fullmatch(username):
        raise ValueError("Invalid username.")

    def reset():
        state = _read_state()
        accounts = _sync(state)
        if username not in accounts:
            raise ValueError("Account does not exist.")
        counters = _counter_values(table_data())
        entry = state[username]
        for direction, counter in zip(("up", "down"), _counter_names(username)):
            entry[direction] = 0
            entry[f"last_{direction}"] = counters.get(counter, 0)
        _save_state(state)

    return _locked(reset)


def remove_account(username):
    if not USERNAME.fullmatch(username):
        raise ValueError("Invalid username.")
    def remove():
        state = _read_state()
        _remove_account_unlocked(state, username, table_data())
        _save_state(state)
    return _locked(remove)
