# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Shared test setup: a throw-away database and backup folder, and signed-in users per role.

Settings are read when app.core.config is imported, so the environment is set
here, before any test imports the app. Values given as environment variables
win over backend/.env.
"""
import itertools
import os
import tempfile

import pytest
from cryptography.fernet import Fernet

_TMP = tempfile.mkdtemp(prefix="ncm-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/ncm-test.db",
    "BACKUP_ROOT": f"{_TMP}/backups",
    "DEVICE_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "SECRET_KEY": "test-secret-key-" + "x" * 40,
    "DEFAULT_ADMIN_USERNAME": "admin",
    "DEFAULT_ADMIN_PASSWORD": "Root-Strong-Pass-42",
    "LOGIN_MAX_ATTEMPTS": "5",
    "LOGIN_LOCKOUT_MINUTES": "15",
    "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
    "ONEDRIVE_CLIENT_ID": "", "GEMINI_API_KEY": "",
})

ADMIN_PASSWORD = os.environ["DEFAULT_ADMIN_PASSWORD"]
USER_PASSWORD = "Test-User-Pass-77"
_names = itertools.count(1)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_login_limiter():
    from app.core.security import login_limiter
    login_limiter.clear()
    yield
    login_limiter.clear()


@pytest.fixture
def db(client):
    from app.core.database import SessionLocal
    session = SessionLocal()
    yield session
    session.close()


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def super_admin(client):
    return login(client, "admin", ADMIN_PASSWORD)


@pytest.fixture
def make_user(client):
    """make_user("operator") -> (user_id, auth headers) for a new, ready-to-use user."""
    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models.user import User

    def _make(role: str, password: str = USER_PASSWORD):
        name = f"{role.replace('_', '')}{next(_names)}"
        s = SessionLocal()
        try:
            u = User(username=name, password_hash=hash_password(password), role=role, is_active=True)
            s.add(u)
            s.commit()
            uid = u.id
        finally:
            s.close()
        return uid, login(client, name, password)
    return _make


@pytest.fixture
def device(client, super_admin):
    """A device row (no real SSH); removed afterwards."""
    r = client.post("/api/devices/", headers=super_admin, json={
        "hostname": f"sw-test-{next(_names)}", "management_ip": "192.0.2.10", "vendor": "arista",
        "username": "netops", "password": "device-pass"})
    assert r.status_code == 200, r.text
    d = r.json()
    yield d
    client.delete(f"/api/devices/{d['id']}", headers=super_admin)
