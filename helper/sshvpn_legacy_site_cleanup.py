"""Remove the retired website allowlist from an existing SSH configuration."""

import re


SSH_MARKER = "# sshvpn-panel shared-port policy; keep this block at the end of sshd_config"
BEGIN = "# sshvpn-site-access begin"
END = "# sshvpn-site-access end"
GLOBAL = "    # sshvpn-site-access global\n"
USER_BLOCK = re.compile(r"(?:Match User [a-z_][a-z0-9_-]{0,31}\n    PermitOpen [^\n]+\n)*\Z")


def without_website_rules(source):
    """Return config without generated rules, refusing edited or partial blocks."""
    if source.count(SSH_MARKER) != 1:
        raise ValueError("The managed SSH VPN block is missing or duplicated.")
    prefix, managed = source.split(SSH_MARKER, 1)
    if prefix.count(BEGIN) != prefix.count(END) or prefix.count(BEGIN) > 1:
        raise ValueError("The legacy website block is malformed.")
    if BEGIN in prefix:
        before, generated = prefix.split(BEGIN + "\n", 1)
        if not generated.endswith(END + "\n"):
            raise ValueError("The legacy website block was edited.")
        if not USER_BLOCK.fullmatch(generated[: -len(END + "\n")]):
            raise ValueError("The legacy website block contains unexpected settings.")
        prefix = before.rstrip("\n") + "\n"
    if managed.count(GLOBAL) > 1:
        raise ValueError("The legacy global website rule is duplicated.")
    if GLOBAL in managed:
        before, remainder = managed.split(GLOBAL, 1)
        line, separator, after = remainder.partition("\n")
        if not separator or not line.startswith("    PermitOpen "):
            raise ValueError("The legacy global website rule was edited.")
        managed = before + after
    if re.search(r"(?mi)^\s*PermitOpen\s", managed):
        raise ValueError("An unmanaged VPN PermitOpen rule exists.")
    return prefix + SSH_MARKER + managed
