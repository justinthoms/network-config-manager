"""Backup storage, retention, cleanup and the endpoints around backup files."""
import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from app.core.config import settings
from app.core.timeutil import utcnow
from app.models.backup import Backup
from app.services.backup import _folder_name


def _add_backup(db, device_id, status="SUCCESS", text="hostname sw\n", age=timedelta(0), **kw):
    root = Path(settings.backup_root)
    root.mkdir(parents=True, exist_ok=True)
    b = Backup(device_id=device_id, status=status, created_at=utcnow() - age, **kw)
    db.add(b)
    db.commit()
    if status == "SUCCESS":
        path = root / f"t-{device_id}-{b.id}.conf"
        path.write_text(text)
        b.file_path = str(path)
        db.commit()
    return b


def test_hostname_cannot_leave_backup_folder():
    for name in ("../../etc", "a/b", "..\\x", "..", "", "."):
        folder = _folder_name(SimpleNamespace(hostname=name, id=7))
        assert "/" not in folder and "\\" not in folder and folder not in ("", ".", "..")
    assert _folder_name(SimpleNamespace(hostname="core-sw1", id=1)) == "core-sw1"


def test_view_and_download_backup(client, super_admin, db, device):
    b = _add_backup(db, device["id"], text="hostname core\nvlan 10\n")
    assert client.get(f"/api/backups/{b.id}/view", headers=super_admin).text.startswith("hostname core")
    r = client.get(f"/api/backups/{b.id}/download", headers=super_admin)
    assert r.status_code == 200 and device["hostname"] in r.headers["content-disposition"]


def test_backup_path_outside_root_is_refused(client, super_admin, db, device):
    b = Backup(device_id=device["id"], status="SUCCESS", file_path="/etc/passwd")
    db.add(b)
    db.commit()
    assert client.get(f"/api/backups/{b.id}/view", headers=super_admin).status_code == 400


def test_file_browser_stays_inside_root(client, super_admin):
    for path in ("../", "../../etc/passwd", "/etc/passwd"):
        r = client.get("/api/files/view", headers=super_admin, params={"path": path})
        assert r.status_code in (400, 404), path


def test_diff_two_backups(client, super_admin, db, device):
    a = _add_backup(db, device["id"], text="hostname sw\nvlan 10\n")
    b = _add_backup(db, device["id"], text="hostname sw\nvlan 10\nvlan 20\n")
    r = client.get("/api/backups/diff", headers=super_admin, params={"backup_a": a.id, "backup_b": b.id})
    assert r.status_code == 200 and r.json()["stats"]["added"] == 1


def test_delete_device_removes_its_rows(client, super_admin, db):
    from app.models.ai_review import AIReview
    from app.models.health import HealthCheck
    d = client.post("/api/devices/", headers=super_admin, json={
        "hostname": "to-delete", "management_ip": "192.0.2.30", "vendor": "juniper",
        "username": "u", "password": "p"}).json()
    b = _add_backup(db, d["id"])
    db.add(AIReview(kind="validate", device_id=d["id"], backup_b=b.id, result="{}"))
    db.add(HealthCheck(device_id=d["id"], status="ok"))
    db.commit()
    assert client.delete(f"/api/devices/{d['id']}", headers=super_admin).status_code == 200
    db.expire_all()
    assert db.query(Backup).filter_by(device_id=d["id"]).count() == 0
    assert db.query(AIReview).filter_by(device_id=d["id"]).count() == 0


def test_retention_keeps_newest(db, device):
    from app.services.scheduler import _cleanup_retention
    rows = [_add_backup(db, device["id"]) for _ in range(5)]
    _cleanup_retention(db, device["id"], keep=2)
    left = [b.id for b in db.query(Backup).filter_by(device_id=device["id"], status="SUCCESS")]
    assert sorted(left) == sorted(b.id for b in rows[-2:])
    assert not Path(rows[0].file_path).exists()


def test_daily_cleanup(db, device):
    from app.services.maintenance import daily_cleanup
    old_failed = _add_backup(db, device["id"], status="FAILED", age=timedelta(days=40)).id
    new_failed = _add_backup(db, device["id"], status="FAILED", age=timedelta(days=2)).id
    stuck = _add_backup(db, device["id"], status="RUNNING", age=timedelta(hours=10)).id
    running = _add_backup(db, device["id"], status="RUNNING").id
    db.expunge_all()
    daily_cleanup()
    assert db.get(Backup, old_failed) is None
    assert db.get(Backup, new_failed) is not None
    assert db.get(Backup, stuck).status == "FAILED"
    assert db.get(Backup, running).status == "RUNNING"


def test_onedrive_only_serves_ncm_backups(client, super_admin, db, device):
    r = client.get("/api/cloud/onedrive/files/SOMEONE-ELSES-FILE/download", headers=super_admin)
    assert r.status_code == 404
    assert client.get("/api/cloud/onedrive/files/SOMEONE-ELSES-FILE/link", headers=super_admin).status_code == 404


# ── background helpers ──

def test_serial_worker_runs_in_order_on_one_thread():
    from app.services.workqueue import SerialWorker
    w, seen, threads = SerialWorker("test-worker"), [], set()

    def job(i):
        threads.add(threading.get_ident())
        time.sleep(0.01)
        seen.append(i)
    for i in range(5):
        w.submit(job, i)
    w.join()
    assert seen == [0, 1, 2, 3, 4] and len(threads) == 1


def test_serial_worker_survives_a_failing_job():
    from app.services.workqueue import SerialWorker
    w, seen = SerialWorker("test-worker-2"), []
    w.submit(lambda: 1 / 0)
    w.submit(seen.append, "after")
    w.join()
    assert seen == ["after"]


def test_login_limiter_forgets_old_keys(monkeypatch):
    from app.core.security import LoginLimiter
    lim = LoginLimiter()
    monkeypatch.setattr(LoginLimiter, "SWEEP_AT", 10)
    for i in range(30):
        lim.failed(f"random{i}", "198.51.100.1")
    # nothing has expired yet, so the sweep keeps them
    assert len(lim._fails) > 10
    clock = [time.monotonic() + 3600]
    monkeypatch.setattr("app.core.security.time.monotonic", lambda: clock[0])
    lim.failed("one-more", "198.51.100.2")
    assert len(lim._fails) <= 3
    assert lim.locked_for("random1", None) == 0
