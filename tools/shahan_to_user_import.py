"""Convert a ShahanPanel MySQL dump to this panel's users-only import archive.

The output contains plaintext VPN passwords. Keep it private.
"""

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from zoneinfo import ZoneInfo


USER_COLUMNS = (
    "id", "username", "password", "email", "mobile", "multiuser", "startdate",
    "finishdate", "enable", "traffic", "referral", "info", "days", "telegramid",
    "userport", "usersub", "shadowport", "guid", "signbox", "signbox2",
)
USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
TEHRAN = ZoneInfo("Asia/Tehran")


def insert_rows(source, table):
    expression = re.compile(r"INSERT INTO\s+`?" + re.escape(table) + r"`?\s+VALUES\s*", re.I)
    matches = list(expression.finditer(source))
    rows = []
    for match in matches:
        row = None
        token = ""
        quoted = False
        field_quoted = False
        escaped = False
        index = match.end()
        while index < len(source):
            char = source[index]
            if quoted:
                if escaped:
                    token += {"0": "\0", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}.get(char, char)
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == "'":
                    if index + 1 < len(source) and source[index + 1] == "'":
                        token += "'"
                        index += 1
                    else:
                        quoted = False
                else:
                    token += char
            elif char == "'":
                if row is None or token.strip():
                    raise ValueError(f"Unexpected quote in {table} INSERT")
                quoted = True
                field_quoted = True
                token = ""
            elif char == "(":
                if row is not None:
                    raise ValueError(f"Nested tuple in {table} INSERT")
                row = []
                token = ""
                field_quoted = False
            elif char in ",)":
                if row is not None:
                    value = token if field_quoted else token.strip()
                    row.append(value if field_quoted or value.upper() != "NULL" else None)
                    token = ""
                    field_quoted = False
                    if char == ")":
                        rows.append(row)
                        row = None
            elif char == ";":
                if row is not None:
                    raise ValueError(f"Unclosed tuple in {table} INSERT")
                break
            elif row is not None:
                token += char
            index += 1
        if quoted or row is not None:
            raise ValueError(f"Unterminated {table} INSERT")
    return rows


def table_columns(source, table):
    match = re.search(r"CREATE TABLE\s+`?" + re.escape(table) + r"`?\s*\((.*?)\)\s*ENGINE=",
                      source, re.I | re.S)
    if not match:
        raise ValueError(f"Missing {table} table schema")
    return tuple(re.findall(r"(?m)^\s*`([^`]+)`\s+", match.group(1)))


def local_date(value, *, end=False):
    day = date.fromisoformat(value)
    stamp = datetime.combine(day + timedelta(days=1) if end else day, time.min, TEHRAN)
    return int(stamp.timestamp()) - (1 if end else 0)


def convert(source):
    if table_columns(source, "users") != USER_COLUMNS:
        raise ValueError("Unexpected Shahan users table layout")
    rows = insert_rows(source, "users")
    if not rows:
        raise ValueError("No Shahan users were found")
    users = []
    seen = set()
    for row in rows:
        if len(row) != len(USER_COLUMNS):
            raise ValueError("An incomplete Shahan user row was found")
        item = dict(zip(USER_COLUMNS, row))
        username, password = item["username"], item["password"]
        if not isinstance(username, str) or not USERNAME.fullmatch(username) or username in seen:
            raise ValueError("An invalid or duplicate Shahan username was found")
        if not isinstance(password, str) or not 1 <= len(password) <= 256 or any(c in password for c in "\r\n\x00"):
            raise ValueError(f"Invalid password for {username}")
        seen.add(username)
        try:
            limit = int(item["multiuser"])
            if not 0 <= limit <= 10000:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid connection limit for {username}") from exc
        if item["traffic"] not in (None, ""):
            raise ValueError(f"The traffic limit for {username} needs an explicit unit mapping")
        finish = item["finishdate"]
        days = item["days"]
        try:
            expires_at = local_date(finish, end=True) if finish else None
            valid_days = int(days) if not finish and days not in (None, "") else None
            created_at = local_date(item["startdate"]) if item["startdate"] else None
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid date or active-day count for {username}") from exc
        if valid_days is not None and not 1 <= valid_days <= 36500:
            raise ValueError(f"Invalid active-day count for {username}")
        status = (item["enable"] or "").lower()
        if status not in ("true", "false", "expired"):
            raise ValueError(f"Unknown account status for {username}")
        referral = item["referral"] or ""
        if "\x00" in referral:
            raise ValueError(f"Invalid referral for {username}")
        users.append({
            "username": username, "password": password,
            "max_connections": limit, "expires_at": expires_at,
            "valid_days": valid_days, "created_at": created_at,
            "enabled": status == "true", "traffic_limit_bytes": None,
            "referral_note": referral,
        })
    return users


def add_member(archive, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.read_text(encoding="utf-8-sig")
    users = convert(source)
    payload = json.dumps(users, ensure_ascii=False, separators=(",", ":")).encode()
    manifest = {
        "format": "sshpanel-user-import-v1", "source": "ShahanPanel",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "account_count": len(users), "users_sha256": hashlib.sha256(payload).hexdigest(),
        "source_timezone": "Asia/Tehran",
    }
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    if args.destination.exists():
        raise SystemExit("Destination already exists; refusing to overwrite it")
    with tarfile.open(args.destination, "x:gz") as archive:
        add_member(archive, "manifest.json", json.dumps(manifest).encode())
        add_member(archive, "users.json", payload)
    args.destination.chmod(0o600)
    print(f"Converted {len(users)} accounts into {args.destination.name}")


if __name__ == "__main__":
    main()
