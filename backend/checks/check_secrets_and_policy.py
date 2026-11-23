# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Secrets stay hidden from roles that may not download backups; only allowed commands run."""
import re
from pathlib import Path

import pytest

from app.core.config import settings
from app.services import command_policy
from app.services.network import LiveCommandError
from check_backups import _add_backup

CONFIG = ("hostname core\n"
          "enable secret 5 SUPERSECRET-HASH\n"
          "snmp-server community TOPSECRETCOMM rw\n"
          "interface Ethernet1\n"
          "   description uplink\n"
          "vlan 10\n")


def _secretless(text):
    return "SUPERSECRET" not in text and "TOPSECRETCOMM" not in text


# ───────────── masking ─────────────

def test_viewer_sees_masked_backup(client, make_user, db, device):
    b = _add_backup(db, device["id"], text=CONFIG)
    _, viewer = make_user("viewer")
    r = client.get(f"/api/backups/{b.id}/view", headers=viewer)
    assert r.status_code == 200 and _secretless(r.text) and "<MASKED>" in r.text
    assert r.headers["x-ncm-masked"] == "1" and r.headers["x-ncm-can-reveal"] == "0"
    # asking for the unmasked text does not help
    r = client.get(f"/api/backups/{b.id}/view?masked=false", headers=viewer)
    assert _secretless(r.text)


def test_operator_can_reveal_and_mask(client, make_user, db, device):
    b = _add_backup(db, device["id"], text=CONFIG)
    _, op = make_user("operator")
    r = client.get(f"/api/backups/{b.id}/view", headers=op)
    assert "SUPERSECRET-HASH" in r.text and r.headers["x-ncm-masked"] == "0"
    r = client.get(f"/api/backups/{b.id}/view?masked=true", headers=op)
    assert _secretless(r.text) and r.headers["x-ncm-masked"] == "1"


def test_view_reports_previous_and_next(client, super_admin, db, device):
    a = _add_backup(db, device["id"], text="hostname a\n")
    b = _add_backup(db, device["id"], text="hostname b\n")
    r = client.get(f"/api/backups/{b.id}/view", headers=super_admin)
    assert r.headers["x-ncm-prev"] == str(a.id) and r.headers["x-ncm-next"] == ""
    r = client.get(f"/api/backups/{a.id}/view", headers=super_admin)
    assert r.headers["x-ncm-next"] == str(b.id)


def test_viewer_file_browser_and_diff_are_masked(client, make_user, db, device):
    a = _add_backup(db, device["id"], text="hostname core\n")
    b = _add_backup(db, device["id"], text=CONFIG)
    _, viewer = make_user("viewer")
    rel = Path(b.file_path).resolve().relative_to(Path(settings.backup_root).resolve()).as_posix()
    r = client.get("/api/files/view", params={"path": rel}, headers=viewer)
    assert r.status_code == 200 and _secretless(r.text)
    d = client.get(f"/api/backups/diff?backup_a={a.id}&backup_b={b.id}", headers=viewer).json()
    assert d["masked"] is True and _secretless(str(d))
    rel_a = Path(a.file_path).resolve().relative_to(Path(settings.backup_root).resolve()).as_posix()
    d = client.get("/api/files/diff", params={"a": rel_a, "b": rel}, headers=viewer).json()
    assert d["masked"] is True and _secretless(str(d))


def test_config_search(client, make_user, super_admin, db, device):
    _add_backup(db, device["id"], text=CONFIG)
    r = client.get("/api/backups/search", params={"q": "uplink", "device_id": device["id"]}, headers=super_admin).json()
    assert r["devices_matched"] == 1
    hit = r["results"][0]["hits"][0]
    assert hit["line"] == 5 and hit["section"] == "interface Ethernet1"
    # a viewer cannot find a secret by guessing it
    _, viewer = make_user("viewer")
    r = client.get("/api/backups/search", params={"q": "TOPSECRETCOMM"}, headers=viewer).json()
    assert r["total_hits"] == 0 and r["masked"] is True
    assert client.get("/api/backups/search", params={"q": "x"}, headers=viewer).status_code == 400


def test_downloads_are_audited(client, super_admin, db, device):
    b = _add_backup(db, device["id"], text="hostname a\n")
    client.get(f"/api/backups/{b.id}/download", headers=super_admin)
    rows = client.get("/api/audit/?limit=20", headers=super_admin).json()
    rows = rows.get("items", rows) if isinstance(rows, dict) else rows
    assert any(x.get("action") == "BACKUP_DOWNLOAD" for x in rows)


def test_folder_zip_download(client, super_admin, db, device):
    _add_backup(db, device["id"], text="hostname z\n")
    r = client.get("/api/files/download", params={"path": ""}, headers=super_admin)
    assert r.status_code == 200 and r.content[:2] == b"PK"


# ───────────── users ─────────────

def test_admin_cannot_take_over_another_admin(client, make_user):
    _, admin = make_user("admin")
    other_id, _ = make_user("admin")
    r = client.patch(f"/api/users/{other_id}", headers=admin, json={"password": "New-Strong-Pass-91"})
    assert r.status_code == 403
    r = client.patch(f"/api/users/{other_id}", headers=admin, json={"is_active": False})
    assert r.status_code == 403
    # harmless edits still work
    assert client.patch(f"/api/users/{other_id}", headers=admin, json={"display_name": "Ops"}).status_code == 200


def test_super_admin_can_reset_admin(client, super_admin, make_user):
    other_id, _ = make_user("admin")
    r = client.patch(f"/api/users/{other_id}", headers=super_admin, json={"password": "New-Strong-Pass-91"})
    assert r.status_code == 200


# ───────────── command policy ─────────────

@pytest.mark.parametrize("vendor", ["huawei", "juniper", "arista", "cisco"])
def test_default_health_commands_are_allowed(vendor, db):
    from app.services.health import DEFAULT_COMMANDS
    for c in DEFAULT_COMMANDS[vendor]:
        command_policy.check(db, vendor, c)


def test_assistant_examples_are_allowed(db):
    from app.services.assistant import SYSTEM
    examples = SYSTEM.split("Useful live commands:", 1)[1]
    for vendor_line in examples.strip().splitlines():
        vendor, cmds = vendor_line.split(":", 1)
        for cmd in re.split(r",\s*", cmds.strip().rstrip(".")):
            command_policy.check(db, vendor.strip().lower(), cmd)


@pytest.mark.parametrize("vendor,cmd", [
    ("arista", "show running-config"),
    ("arista", "show running-config | section bgp"),
    ("juniper", "show configuration | display set"),
    ("huawei", "display current-configuration"),
    ("huawei", "display saved-configuration"),
    ("arista", "show tech-support"),
    ("arista", "show users"),
    ("huawei", "display diagnostic-information"),
    ("arista", "show lldp traffic"),           # not on the allowed list
    ("huawei", "display aaa local-user"),
])
def test_sensitive_or_unlisted_commands_are_refused(vendor, cmd, db):
    with pytest.raises(LiveCommandError):
        command_policy.check(db, vendor, cmd)


def test_only_super_admin_edits_allowed_commands(client, super_admin, make_user):
    _, admin = make_user("admin")
    body = {"extra": {"arista": ["show lldp traffic"]}}
    assert client.put("/api/health/command-policy", headers=admin, json=body).status_code == 403
    r = client.put("/api/health/command-policy", headers=super_admin, json=body)
    assert r.status_code == 200 and r.json()["vendors"]["arista"]["extra"] == ["show lldp traffic"]
    ok = client.post("/api/health/command-policy/check", headers=admin,
                     json={"vendor": "arista", "command": "show lldp traffic"}).json()
    assert ok["ok"] is True
    # config-printing commands cannot be added
    bad = {"extra": {"arista": ["show running-config all"]}}
    assert client.put("/api/health/command-policy", headers=super_admin, json=bad).status_code == 400
    client.put("/api/health/command-policy", headers=super_admin, json={"extra": {"arista": []}})


def test_health_commands_must_be_allowed(client, super_admin):
    body = {"commands": {"arista": ["show version", "show running-config"]}}
    r = client.put("/api/health/settings", headers=super_admin, json=body)
    assert r.status_code == 400 and "configuration" in r.json()["detail"]


def test_admin_cannot_change_health_commands(client, make_user):
    _, admin = make_user("admin")
    r = client.put("/api/health/settings", headers=admin, json={"commands": {"arista": ["show version"]}})
    assert r.status_code == 403
