# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Cisco IOS / IOS-XE support: driver, commands, config backup command and health parsing."""
import pytest

from app.services import command_policy, health
from app.services.network import SUPPORTED, LiveCommandError, validate_show_command

VERSION = ("Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(4)E7, RELEASE SOFTWARE (fc2)\n"
           "cisco WS-C2960X-48TS-L (APM86XXX) processor with 524288K bytes of memory.\n"
           "Switch uptime is 2 years, 3 weeks, 1 day\n")
CPU = "CPU utilization for five seconds: 7%/0%; one minute: 8%; five minutes: 9%\n"
MEM = "Processor Pool Total:  262144000 Used:  104857600 Free:  157286400\n"
IFACES = ("Port      Name               Status       Vlan       Duplex  Speed Type\n"
          "Gi1/0/1                      connected    10         a-full a-1000 10/100/1000BaseTX\n"
          "Gi1/0/2                      notconnect   1          auto   auto 10/100/1000BaseTX\n"
          "Gi1/0/3                      err-disabled 1          auto   auto 10/100/1000BaseTX\n"
          "Po1                          connected    trunk      a-full a-1000\n")
LOGS = ("Oct 10 11:00:01.000: %LINK-3-UPDOWN: Interface GigabitEthernet1/0/3, changed state to down\n"
        "Oct 10 11:00:02.000: %LINEPROTO-5-UPDOWN: Line protocol on Interface Gi1/0/1, changed state to up\n")


def test_cisco_is_a_supported_driver():
    assert SUPPORTED["cisco"] == "cisco_ios"


def test_cisco_show_commands_validate():
    for c in ("show version", "show interfaces status", "show ip route", "show logging | include DOWN"):
        validate_show_command("cisco", c)
    for bad in ("display version", "reload", "configure terminal", "write memory", "show version; reload"):
        with pytest.raises(LiveCommandError):
            validate_show_command("cisco", bad)


def test_cisco_default_commands_are_allowed(db):
    for c in health.DEFAULT_COMMANDS["cisco"]:
        command_policy.check(db, "cisco", c)


@pytest.mark.parametrize("cmd", ["show running-config", "show startup-config", "show tech-support",
                                 "show users", "show archive config"])
def test_cisco_config_and_secret_commands_refused(cmd, db):
    with pytest.raises(LiveCommandError):
        command_policy.check(db, "cisco", cmd)


def test_cisco_backup_uses_running_config(monkeypatch):
    from app.services import backup
    calls = []

    class FakeConn:
        def check_enable_mode(self): return True
        def enable(self): calls.append("enable")
        def send_command_timing(self, c, **k): calls.append(c); return ""
        def send_command(self, c, **k):
            calls.append(c)
            return "hostname R1\n" + "!\n" * 40 + "interface Gi1/0/1\n end\n"

    out = backup._get_config(type("D", (), {"vendor": "cisco"})(), FakeConn())
    assert "show running-config" in calls and "hostname R1" in out


def test_cisco_health_parsing():
    results = [
        {"command": "show version", "output": VERSION, "ok": True},
        {"command": "show processes cpu sorted", "output": CPU, "ok": True},
        {"command": "show processes memory sorted", "output": MEM, "ok": True},
        {"command": "show interfaces status", "output": IFACES, "ok": True},
        {"command": "show logging", "output": LOGS, "ok": True},
    ]
    a = health.analyse("cisco", results)
    assert a["cpu"] == 7
    assert a["memory"] == 40                      # 104857600 / 262144000
    assert a["facts"].get("version", "").startswith("15.2(4)E7")
    assert a["facts"].get("model") == "WS-C2960X-48TS-L"
    counts = a["interfaces"]["counts"]
    assert counts.get("up") == 2                   # Gi1/0/1 + Po1
    assert "Gi1/0/3" in a["interfaces"]["err_disabled"]
    assert "Gi1/0/2" in a["interfaces"]["down"]
    assert health.status_of(a) in ("ok", "warning")   # err-disabled is a warning


def test_api_rejects_unknown_vendor_accepts_cisco(client, super_admin):
    bad = client.post("/api/devices/", headers=super_admin, json={
        "hostname": "x-unknown", "management_ip": "192.0.2.50", "vendor": "mikrotik",
        "username": "a", "password": "device-pass"})
    assert bad.status_code == 400 and "Unsupported vendor" in bad.json()["detail"]
    ok = client.post("/api/devices/", headers=super_admin, json={
        "hostname": "x-cisco-1", "management_ip": "192.0.2.51", "vendor": "Cisco",
        "username": "netops", "password": "device-pass", "site": "KOCHI"})
    assert ok.status_code == 200 and ok.json()["vendor"] == "cisco"
    client.delete(f"/api/devices/{ok.json()['id']}", headers=super_admin)


# ───────────── Telnet ─────────────

def test_netmiko_params_ssh_and_telnet():
    from app.services import network
    from app.core.crypto import encrypt_secret

    class D:
        vendor = "cisco"; management_ip = "192.0.2.9"; username = "u"
        password_encrypted = encrypt_secret("p"); ssh_port = 22; protocol = "ssh"
    d = D()
    assert network.netmiko_params(d)["device_type"] == "cisco_ios"
    assert network.netmiko_params(d)["port"] == 22
    d.protocol = "telnet"; d.ssh_port = None
    p = network.netmiko_params(d)
    assert p["device_type"] == "cisco_ios_telnet" and p["port"] == 23
    for vendor, base in [("huawei", "huawei"), ("juniper", "juniper_junos"), ("arista", "arista_eos")]:
        d.vendor = vendor
        assert network.netmiko_params(d)["device_type"] == base + "_telnet"


def test_create_telnet_device_and_reject_bad_protocol(client, super_admin):
    r = client.post("/api/devices/", headers=super_admin, json={
        "hostname": "tel-sw-1", "management_ip": "192.0.2.90", "vendor": "cisco",
        "username": "netops", "password": "device-pass", "protocol": "telnet"})
    assert r.status_code == 200 and r.json()["protocol"] == "telnet"
    did = r.json()["id"]
    bad = client.patch(f"/api/devices/{did}", headers=super_admin, json={"protocol": "rlogin"})
    assert bad.status_code == 400
    # default is ssh
    r2 = client.post("/api/devices/", headers=super_admin, json={
        "hostname": "ssh-sw-1", "management_ip": "192.0.2.91", "vendor": "arista",
        "username": "netops", "password": "device-pass"})
    assert r2.json()["protocol"] == "ssh"
    client.delete(f"/api/devices/{did}", headers=super_admin)
    client.delete(f"/api/devices/{r2.json()['id']}", headers=super_admin)
