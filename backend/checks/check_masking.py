# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Secrets must never reach an AI provider."""
import pytest

from app.services.ai_review import IPMasker, mask_secrets

SECRET_LINES = [
    ("ntp authentication-key 1 md5 7 0605063D", "0605063D"),
    ("ip ospf message-digest-key 1 md5 7 ABCD1234", "ABCD1234"),
    ("snmp-server user u g v3 localized 0x1 auth sha512 0xaa11 priv aes256 0xbb22", "0xaa11"),
    ("snmp-server user u g v3 localized 0x1 auth sha512 0xaa11 priv aes256 0xbb22", "0xbb22"),
    ("snmp-server user u g v3 auth sha 0xaa11 priv aes 0xbb22", "0xbb22"),
    (" ospf authentication-mode hmac-sha256 1 cipher %@%@abc%@%@", "%@%@abc%@%@"),
    (" ospf authentication-mode md5 1 plain K3yValue", "K3yValue"),
    (" isis authentication-mode simple plain K3yValue", "K3yValue"),
    ('set system root-authentication encrypted-password "$6$salt$hash"', "$6$salt$hash"),
    ("set system login user ops authentication encrypted-password $6$abc$def", "$6$abc$def"),
    ("set snmp community S3cretComm authorization read-only", "S3cretComm"),
    ("username admin secret sha512 $6$xyz$abc", "$6$xyz$abc"),
    ("username admin privilege 15 secret 0 PlainPass1", "PlainPass1"),
    ("enable password MyEnable1", "MyEnable1"),
    ("snmp-agent community read cipher %^%#abc%^%#", "%^%#abc%^%#"),
    (" local-user admin password irreversible-cipher $1c$abc$", "$1c$abc$"),
    ("tacacs-server key 7 070C285F4D06", "070C285F4D06"),
    ("radius-server host 10.0.0.1 key S3cretRad", "S3cretRad"),
    (" pre-shared-key cipher %$%$abc%$%$", "%$%$abc%$%$"),
    ("set security ike policy p pre-shared-key ascii-text \"$9$abcdef\"", "$9$abcdef"),
]


@pytest.mark.parametrize("line,secret", SECRET_LINES)
def test_secret_is_masked(line, secret):
    out = mask_secrets(line)
    assert secret not in out, out
    assert "<MASKED" in out


def test_private_key_block_is_masked():
    text = "crypto key\n-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\nend"
    out = mask_secrets(text)
    assert "MIIabc" not in out and out.endswith("end")


def test_weak_default_is_flagged():
    assert "<MASKED-WEAK-DEFAULT>" in mask_secrets("snmp-server community public ro")


@pytest.mark.parametrize("line", [
    "interface Ethernet1",
    " description uplink to core",
    " switchport mode trunk",
    "aaa authentication login default local",
    "vlan batch 10 20 30",
    "username ops privilege 15 role network-admin",
])
def test_ordinary_lines_unchanged(line):
    assert mask_secrets(line) == line


def test_mask_does_not_run_onto_next_line():
    assert mask_secrets("authentication-type password\nssh server enable").endswith("\nssh server enable")


def test_ip_masker_round_trip():
    m = IPMasker()
    masked = m.mask("peer 10.1.1.1 and 10.1.1.2, again 10.1.1.1")
    assert "10.1.1" not in masked
    assert masked.count(masked.split()[1]) == 2         # same address -> same placeholder
    assert m.unmask(masked) == "peer 10.1.1.1 and 10.1.1.2, again 10.1.1.1"
