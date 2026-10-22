# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from app.services.diff import compare, quick_stats

A = "!Last configuration was updated at 2026-10-01\nsysname R1\nvlan batch 10 20\nsnmp-agent community read X\nreturn\n"
B = "!Last configuration was updated at 2026-10-02\nsysname R1\nvlan batch 10 20 30\nsnmp-agent community read X\nntp-service unicast-server 10.0.0.1\nreturn\n"


def test_volatile_lines_ignored():
    same = compare(A, A.replace("2026-10-01", "2026-12-31"))
    assert same["changed"] is False


def test_stats_and_rows():
    r = compare(A, B)
    assert r["changed"] is True
    assert r["stats"]["modified"] == 1
    assert r["stats"]["added"] - r["stats"]["modified"] == 1
    kinds = [row["t"] for row in r["rows"] if row["t"] != "eq"]
    assert kinds == ["mod", "add"]


def test_quick_stats():
    assert quick_stats(A, B) == (2, 1)


def test_fingerprint_ignores_timestamps():
    from app.services.diff import config_fingerprint
    assert config_fingerprint(A) == config_fingerprint(A.replace("2026-10-01", "2027-01-01"))
    assert config_fingerprint(A) != config_fingerprint(B)


def test_live_command_validation():
    import pytest
    from app.services.network import validate_show_command, LiveCommandError
    assert validate_show_command("huawei", "display interface brief")
    assert validate_show_command("juniper", "show log messages | match LINK_DOWN | last 20")
    for vendor, cmd in [("huawei", "system-view"), ("juniper", "request system reboot"),
                        ("arista", "show run | redirect flash:x"), ("huawei", "display x; reboot"),
                        ("juniper", "show configuration | save /tmp/x"), ("arista", "configure terminal")]:
        with pytest.raises(LiveCommandError):
            validate_show_command(vendor, cmd)
