# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Usage reporting sends only version + hostname, is off in tests, and only a Super Admin can change it."""
from app.core.database import SessionLocal
from app.services import telemetry


def test_payload_has_no_ip_or_device_data(client):
    db = SessionLocal()
    try:
        payload = telemetry.collect(db)
    finally:
        db.close()
    # the ENTIRE payload is these four keys and nothing else
    assert set(payload) == {"project", "version", "hostname", "instance_id"}
    blob = str(payload).lower()
    for leaked in ("192.", "10.", "ip", "device", "password", "cpu", "mem", "disk", "kernel"):
        assert leaked not in payload  # keys
    assert payload["project"] == "network-config-manager"


def test_instance_id_is_random_and_stable(client):
    db = SessionLocal()
    try:
        a = telemetry.collect(db)["instance_id"]
        b = telemetry.collect(db)["instance_id"]
    finally:
        db.close()
    assert a == b and len(a) == 32            # a UUID hex, not derived from the machine


def test_disabled_in_tests(client):
    db = SessionLocal()
    try:
        assert telemetry.is_enabled(db) is False     # TELEMETRY_ENABLED=false in the test env
        assert telemetry.ping_once() is False         # never sends, never raises
    finally:
        db.close()


def test_admin_sees_but_cannot_change(client, make_user):
    _, admin = make_user("admin")
    r = client.get("/api/security/telemetry", headers=admin)
    assert r.status_code == 200
    body = r.json()
    assert body["can_change"] is False and set(body["sends"]) == {"project", "version", "hostname", "instance_id"}
    assert client.put("/api/security/telemetry", headers=admin, json={"enabled": True}).status_code == 403


def test_viewer_cannot_see_security(client, make_user):
    _, viewer = make_user("viewer")
    assert client.get("/api/security/telemetry", headers=viewer).status_code == 403


def test_operator_cannot_see_security(client, make_user):
    _, op = make_user("operator")
    assert client.get("/api/security/telemetry", headers=op).status_code == 403


def test_super_admin_toggles(client, super_admin, db):
    from app.services import settings_store as store
    r = client.put("/api/security/telemetry", headers=super_admin, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert store.get(db, telemetry.K_ENABLED) == "1"
    assert client.put("/api/security/telemetry", headers=super_admin, json={"enabled": False}).json()["enabled"] is False
    # the stored choice overrides the .env default
    assert telemetry.is_enabled(db) is False


def test_ping_sends_only_minimal_payload(client, super_admin, db, monkeypatch):
    sent = {}
    monkeypatch.setattr(telemetry, "_post", lambda path, payload, headers: sent.setdefault(path, payload) or {"ok": True})
    client.put("/api/security/telemetry", headers=super_admin, json={"enabled": True})
    store_db = SessionLocal()
    try:
        from app.services import settings_store as store
        store.set(store_db, telemetry.K_SECRET, "x")
        store.set(store_db, telemetry.K_INSTANCE, "i" * 32)
    finally:
        store_db.close()
    assert telemetry.ping_once() is True
    assert set(sent["/api/v1/heartbeat"]) == {"project", "version", "hostname", "instance_id"}
    client.put("/api/security/telemetry", headers=super_admin, json={"enabled": False})
