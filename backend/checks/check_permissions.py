# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Role checks: who may do what."""
import pytest

NEW_DEVICE = {"hostname": "perm-test", "management_ip": "192.0.2.20", "vendor": "huawei",
              "username": "u", "password": "p"}


@pytest.mark.parametrize("role,expected", [("viewer", 403), ("operator", 200), ("admin", 200)])
def test_create_device_by_role(client, make_user, super_admin, role, expected):
    _, headers = make_user(role)
    r = client.post("/api/devices/", headers=headers, json={**NEW_DEVICE, "hostname": f"perm-{role}"})
    assert r.status_code == expected
    if r.status_code == 200:
        client.delete(f"/api/devices/{r.json()['id']}", headers=super_admin)


def test_operator_cannot_delete_device(client, make_user, device):
    _, headers = make_user("operator")
    assert client.delete(f"/api/devices/{device['id']}", headers=headers).status_code == 403


def test_viewer_cannot_list_users_or_audit(client, make_user):
    _, headers = make_user("viewer")
    assert client.get("/api/users/", headers=headers).status_code == 403
    assert client.get("/api/audit/", headers=headers).status_code == 403


def test_device_list_never_returns_password(client, super_admin, device):
    rows = client.get("/api/devices/", headers=super_admin).json()
    row = next(d for d in rows if d["id"] == device["id"])
    assert "password" not in row and "password_encrypted" not in row


# ── users: an Admin must not be able to take over a Super Admin ──

def test_admin_cannot_reset_super_admin_password(client, make_user, super_admin):
    _, admin = make_user("admin")
    sa_id = client.get("/api/auth/me", headers=super_admin).json()["id"]
    assert client.patch(f"/api/users/{sa_id}", headers=admin, json={"password": "Hijack-Pass-99x"}).status_code == 403
    assert client.patch(f"/api/users/{sa_id}", headers=admin, json={"is_active": False}).status_code == 403
    assert client.patch(f"/api/users/{sa_id}", headers=admin, json={"role": "viewer"}).status_code == 403


def test_admin_cannot_create_or_grant_super_admin(client, make_user):
    _, admin = make_user("admin")
    r = client.post("/api/users/", headers=admin,
                    json={"username": "newsuper", "password": "Strong-Pass-55x", "role": "super_admin"})
    assert r.status_code == 403
    other_id, _ = make_user("viewer")
    assert client.patch(f"/api/users/{other_id}", headers=admin, json={"role": "super_admin"}).status_code == 403


def test_admin_can_manage_lower_roles(client, make_user):
    _, admin = make_user("admin")
    other_id, _ = make_user("viewer")
    r = client.patch(f"/api/users/{other_id}", headers=admin, json={"role": "operator"})
    assert r.status_code == 200 and r.json()["role"] == "operator"


def test_user_cannot_change_own_role_or_disable_self(client, make_user):
    uid, admin = make_user("admin")
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"role": "viewer"}).status_code == 400
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"is_active": False}).status_code == 400
    # sending the same role back is fine
    assert client.patch(f"/api/users/{uid}", headers=admin, json={"role": "admin", "display_name": "A"}).status_code == 200


def test_new_user_password_policy(client, super_admin):
    r = client.post("/api/users/", headers=super_admin, json={"username": "weakling", "password": "admin123"})
    assert r.status_code == 400


# ── health check commands: Super Admin only ──

def test_only_super_admin_changes_health_commands(client, make_user, super_admin):
    cmds = {"arista": ["show version", "show interfaces status"]}
    _, admin = make_user("admin")
    _, operator = make_user("operator")
    assert client.put("/api/health/settings", headers=admin, json={"commands": cmds}).status_code == 403
    assert client.put("/api/health/settings", headers=admin, json={"retention": 40}).status_code == 200
    assert client.put("/api/health/settings", headers=operator, json={"retention": 40}).status_code == 403
    assert client.put("/api/health/settings", headers=super_admin, json={"commands": cmds}).status_code == 200
    client.put("/api/health/settings", headers=super_admin, json={"commands": {"arista": None}})


def test_health_commands_must_be_read_only(client, super_admin):
    for bad in ("configure terminal", "reload", "show version; reload", "display version"):
        r = client.put("/api/health/settings", headers=super_admin, json={"commands": {"arista": [bad]}})
        assert r.status_code == 400, bad
