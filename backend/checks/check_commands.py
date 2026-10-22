# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Only read-only show/display commands may reach a device."""
import pytest

from app.services.network import LiveCommandError, validate_show_command

ALLOWED = [
    ("huawei", "display version"),
    ("huawei", "display interface brief"),
    ("huawei", "display logbuffer | include DOWN"),
    ("huawei", "display current-configuration"),
    ("juniper", "show interfaces terse"),
    ("juniper", "show configuration | display set"),
    ("juniper", "show log messages | match LINK_ | last 30"),
    ("arista", "show version"),
    ("arista", "show interfaces status"),
    ("arista", "show running-config | section bgp"),
]

REFUSED = [
    ("huawei", "show version"),               # wrong verb for the vendor
    ("arista", "display version"),
    ("arista", "configure terminal"),
    ("arista", "reload"),
    ("arista", "bash ls"),
    ("arista", "enable"),
    ("juniper", "request system reboot"),
    ("juniper", "set system host-name x"),
    ("huawei", "system-view"),
    ("huawei", "display version | save flash:/x"),
    ("arista", "show run | redirect flash:x"),
    ("arista", "show version; reload"),
    ("arista", "show version && reload"),
    ("arista", "show version > flash:x"),
    ("arista", "show `reload`"),
    ("arista", "show $(reload)"),
    ("arista", "show clear counters"),
    ("arista", "show file systems"),
    ("arista", ""),
    ("arista", "show " + "x" * 300),
    ("cisco", "show version"),                # unsupported vendor
]


def test_assistant_example_commands_are_accepted():
    """Every live command the assistant's prompt suggests must pass the check."""
    import re
    from app.services.assistant import SYSTEM
    examples = SYSTEM.split("Useful live commands:", 1)[1]
    for vendor_line in examples.strip().splitlines():
        vendor, cmds = vendor_line.split(":", 1)
        for cmd in re.split(r",\s*", cmds.strip().rstrip(".")):
            validate_show_command(vendor.strip().lower(), cmd)


@pytest.mark.parametrize("vendor,cmd", ALLOWED)
def test_allowed(vendor, cmd):
    assert validate_show_command(vendor, cmd)


@pytest.mark.parametrize("vendor,cmd", REFUSED)
def test_refused(vendor, cmd):
    with pytest.raises(LiveCommandError):
        validate_show_command(vendor, cmd)


def test_whitespace_is_normalised():
    assert validate_show_command("arista", "  show   version  ") == "show version"


def test_vendor_is_case_insensitive():
    assert validate_show_command("Arista", "show version") == "show version"
