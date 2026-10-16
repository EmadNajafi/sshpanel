"""Best-effort ISP lookup for a live client's public IP address."""

import hashlib
import ipaddress
import json
from urllib.error import URLError
from urllib.request import Request, urlopen

from django.core.cache import cache


def lookup_isp(value):
    try:
        address = ipaddress.ip_address(value)
    except (ValueError, TypeError):
        return None
    if not address.is_global:
        return None

    ip = address.compressed
    key = "sshvpn-isp:" + hashlib.sha256(ip.encode("ascii")).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached or None

    isp = None
    try:
        request = Request(f"https://ipwho.is/{ip}", headers={"Accept": "application/json"})
        with urlopen(request, timeout=2) as response:
            payload = json.loads(response.read(16385))
        if (isinstance(payload, dict) and payload.get("success") is True
                and payload.get("ip") == ip and isinstance(payload.get("connection"), dict)):
            name = payload["connection"].get("isp")
            if isinstance(name, str) and name.strip():
                isp = name.strip()[:160]
    except (URLError, OSError, ValueError, TypeError, OverflowError):
        pass

    cache.set(key, isp or "", timeout=86400 if isp else 300)
    return isp
