# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from datetime import datetime, timedelta, timezone

from jose import jwt

from ncm_fixtures import ADMIN_PASSWORD, USER_PASSWORD


def test_login_and_me(client, super_admin):
    me = client.get("/api/auth/me", headers=super_admin).json()
    assert me["username"] == "admin"
    assert me["role"] == "super_admin"
    assert "users.delete" in me["permissions"]


def test_wrong_password_is_refused(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert r.status_code == 401


def test_lockout_after_max_attempts(client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    # even the right password is refused while locked
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 429


def test_unknown_username_is_also_locked(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "nobody", "password": "bad"})
    assert client.post("/api/auth/login", json={"username": "nobody", "password": "bad"}).status_code == 429


def test_no_token_and_bad_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_jwt_without_access_type_is_refused(client):
    """Other JWTs signed with the same key (e.g. the Google OAuth state) are not sign-ins."""
    from app.core.config import settings
    token = jwt.encode({"sub": "admin", "purpose": "gdrive-oauth",
                        "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, "HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_weak_password_must_be_changed(client, make_user):
    _, headers = make_user("viewer", password="password123")     # on the common-password list
    assert client.get("/api/devices/", headers=headers).json()["detail"] == "password_change_required"
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_change_password_signs_out_other_sessions(client, make_user):
    uid, old_headers = make_user("operator")
    r = client.patch("/api/auth/me", headers=old_headers,
                     json={"current_password": USER_PASSWORD, "new_password": "Brand-New-Pass-99"})
    assert r.status_code == 200
    new_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get("/api/auth/me", headers=old_headers).status_code == 401
    assert client.get("/api/auth/me", headers=new_headers).status_code == 200


def test_change_password_needs_current_password(client, make_user):
    _, headers = make_user("operator")
    r = client.patch("/api/auth/me", headers=headers,
                     json={"current_password": "wrong", "new_password": "Brand-New-Pass-99"})
    assert r.status_code == 400


def test_logout_all(client, make_user):
    _, headers = make_user("viewer")
    assert client.post("/api/auth/logout-all", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
