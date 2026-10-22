# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""'Back up all devices' runs on the server, in parallel."""
import threading
import time

import pytest

from app.models.device import Device

FAKE_SSH_SECONDS = 0.3


@pytest.fixture
def many_devices(db):
    ids = []
    for i in range(30):
        d = Device(hostname=f"bulk-{i:03d}", management_ip="192.0.2.50", vendor="huawei",
                   username="u", password_encrypted="x", enabled=True)
        db.add(d)
        db.commit()
        ids.append(d.id)
    yield ids
    db.query(Device).filter(Device.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


@pytest.fixture
def fake_ssh(monkeypatch):
    """Replace the SSH backup with a 0.3 s sleep; bulk-007 always fails."""
    seen, active, peak, lock = [], [0], [0], threading.Lock()

    def fake_run_backup(device, db):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(FAKE_SSH_SECONDS)
        with lock:
            active[0] -= 1
            seen.append(device.hostname)
        if device.hostname == "bulk-007":
            return {"success": False, "error": "SSH timeout"}
        return {"success": True, "changed": device.hostname == "bulk-001"}

    monkeypatch.setattr("app.services.backup.run_backup", fake_run_backup)
    return {"seen": seen, "peak": peak}


def _wait(client, headers, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = client.get("/api/backups/run-all", headers=headers).json()
        if not s.get("running"):
            return s
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_backup_all_runs_in_parallel_on_the_server(client, super_admin, many_devices, fake_ssh):
    started = time.time()
    r = client.post("/api/backups/run-all", headers=super_admin)
    assert r.status_code == 202 and r.json()["running"]
    # a second start while running is refused
    assert client.post("/api/backups/run-all", headers=super_admin).status_code == 409
    s = _wait(client, super_admin)
    took = time.time() - started

    total = s["total"]
    assert total >= 30 and s["done"] == total
    assert s["failed"] == 1 and s["failed_hosts"][0]["host"] == "bulk-007"
    assert s["changed"] == 1 and s["ok"] == total - 1
    # 30 devices x 0.3 s one by one would take 9 s; 10 at a time takes about 1 s
    assert fake_ssh["peak"][0] > 1
    assert took < total * FAKE_SSH_SECONDS / 2


def test_viewer_cannot_start_backup_all(client, make_user):
    _, viewer = make_user("viewer")
    assert client.post("/api/backups/run-all", headers=viewer).status_code == 403
    assert client.get("/api/backups/run-all", headers=viewer).status_code == 200


def test_interrupted_run_is_not_reported_as_running(db):
    """A run cut off by a restart left running=true behind; nobody holds the lock, so it is over."""
    from app.services import bulk_backup
    from app.services import settings_store as store
    store.set_json(db, bulk_backup.K_STATE, {"running": True, "total": 5, "done": 2})
    s = bulk_backup.state(db)
    assert s["running"] is False and s["interrupted"] is True
