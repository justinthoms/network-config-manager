# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Which commands NCM may run on a device, outside of the config backup itself.

Health checks and the assistant's live commands run only commands that pass
three checks, in order:

1. validate_show_command: one read-only show/display command, no dangerous
   words, only harmless output filters after a pipe.
2. Never a command that prints the configuration or other secrets (running
   config, tech-support, ...). The configuration is read by the backup job and
   shown masked; health output is visible to every role with health.view.
3. The command must start with an allowed command: the built-in list below,
   plus extra commands that only a Super Admin can add.
"""
import re

from app.services import settings_store as store
from app.services.network import LiveCommandError, validate_show_command

K_EXTRA = "commands.allowed."          # + vendor, JSON list of extra allowed commands
MAX_EXTRA = 50

# Built-in safe commands. A command is allowed when it is one of these, or starts
# with one of these followed by a space ("show interfaces" allows "show interfaces status").
BUILTIN = {
    "huawei": [
        "display version", "display cpu-usage", "display memory-usage", "display device", "display fan",
        "display power", "display temperature", "display alarm", "display interface", "display ip interface",
        "display ip routing-table", "display arp", "display mac-address", "display vlan", "display port vlan",
        "display lldp neighbor", "display stp", "display eth-trunk", "display ospf peer", "display ospf brief",
        "display bgp peer", "display bgp vpnv4 all peer", "display isis peer", "display mpls lsp",
        "display bfd session", "display vrrp", "display logbuffer", "display trapbuffer", "display clock",
        "display elabel", "display esn", "display license", "display transceiver", "display health",
        "display ntp-service status", "display patch-information", "display startup", "display cpu",
        "display ip vpn-instance",
    ],
    "juniper": [
        "show version", "show chassis", "show system alarms", "show system uptime", "show system processes",
        "show system storage", "show system commit", "show interfaces", "show route", "show arp",
        "show ethernet-switching", "show lldp neighbors", "show spanning-tree", "show lacp", "show ospf",
        "show bgp", "show isis", "show mpls", "show bfd", "show vrrp", "show vlans", "show virtual-chassis",
        "show ntp", "show log messages",
    ],
    "arista": [
        "show version", "show processes", "show system environment", "show environment", "show interfaces",
        "show ip interface", "show ip route", "show ipv6 route", "show arp", "show mac address-table",
        "show vlan", "show lldp neighbors", "show spanning-tree", "show port-channel", "show ip ospf",
        "show ip bgp", "show bgp", "show isis", "show mlag", "show logging", "show clock", "show uptime",
        "show inventory", "show ntp", "show vrrp", "show bfd", "show hardware",
    ],
}

# Commands that print configuration or secrets: never allowed, not even as an extra command.
_SENSITIVE = re.compile(
    r"\b(running-config|startup-config|current-configuration|saved-configuration|configuration|config|"
    r"tech-support|tech|diagnostic-information|diag|secret|password|key-chain|keychain|pki|certificate|"
    r"crypto|aaa|snmp-agent|snmp|local-user|users?|history|file|dir)\b",
    re.IGNORECASE,
)


def _norm(cmd: str) -> str:
    return " ".join((cmd or "").split()).lower()


def builtin(vendor: str) -> list[str]:
    return list(BUILTIN.get((vendor or "").lower(), []))


def extra(db, vendor: str) -> list[str]:
    items = store.get_json(db, K_EXTRA + (vendor or "").lower())
    return [x for x in items if isinstance(x, str)] if isinstance(items, list) else []


def allowed(db, vendor: str) -> list[str]:
    return builtin(vendor) + extra(db, vendor)


def _main_part(cmd: str) -> str:
    return _norm(cmd.split("|", 1)[0])


def check_sensitive(cmd: str):
    if _SENSITIVE.search(_main_part(cmd)):
        raise LiveCommandError("Commands that show the configuration or secrets are not allowed here. "
                               "Use the config backup instead, which hides secrets.")


def check(db, vendor: str, command: str) -> str:
    """Return the cleaned command, or raise LiveCommandError saying why it is refused."""
    cmd = validate_show_command(vendor, command)
    check_sensitive(cmd)
    main = _main_part(cmd)
    for prefix in allowed(db, vendor):
        p = _norm(prefix)
        if main == p or main.startswith(p + " "):
            return cmd
    raise LiveCommandError("This command is not on the allowed list. A Super Admin can allow it in "
                           "Health check settings, under Allowed commands.")


def clean_extra(vendor: str, items: list[str]) -> list[str]:
    """Validate the extra allowed commands a Super Admin wants to save."""
    out = []
    for raw in items:
        cmd = " ".join((raw or "").split())
        if not cmd:
            continue
        if "|" in cmd:
            raise LiveCommandError(f"'{cmd}': give the command without an output filter (| ...)")
        cmd = validate_show_command(vendor, cmd)
        check_sensitive(cmd)
        if len(cmd.split()) < 2:
            raise LiveCommandError(f"'{cmd}': too broad, give at least '{cmd} <something>'")
        if _norm(cmd) not in {_norm(x) for x in out} and _norm(cmd) not in {_norm(x) for x in builtin(vendor)}:
            out.append(cmd)
    if len(out) > MAX_EXTRA:
        raise LiveCommandError(f"At most {MAX_EXTRA} extra commands per vendor")
    return out


def policy_view(db) -> dict:
    return {v: {"builtin": builtin(v), "extra": extra(db, v)} for v in BUILTIN}
