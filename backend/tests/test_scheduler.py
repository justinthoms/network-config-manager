"""Scheduled jobs follow the database, and only one process runs them."""
from app.models.schedule import BackupSchedule
from app.services import scheduler as sched


def _job(device_id):
    return sched.scheduler.get_job(f"backup-device-{device_id}")


def test_this_process_is_the_runner(client):
    assert sched.is_leader()


def test_sync_picks_up_changes_made_elsewhere(client, db, device):
    """A change written by another worker process (straight to the DB) is applied at the next sync."""
    db.query(BackupSchedule).filter_by(device_id=device["id"]).delete()
    db.add(BackupSchedule(device_id=device["id"], enabled=True, interval_minutes=60))
    db.commit()
    sched.sync_jobs()
    job = _job(device["id"])
    assert job is not None and job.trigger.interval.total_seconds() == 3600

    db.query(BackupSchedule).filter_by(device_id=device["id"]).update({"interval_minutes": 120})
    db.commit()
    sched.sync_jobs()
    assert _job(device["id"]).trigger.interval.total_seconds() == 7200

    db.query(BackupSchedule).filter_by(device_id=device["id"]).update({"enabled": False})
    db.commit()
    sched.sync_jobs()
    assert _job(device["id"]) is None


def test_sync_leaves_unchanged_jobs_alone(client, db, device):
    db.query(BackupSchedule).filter_by(device_id=device["id"]).delete()
    db.add(BackupSchedule(device_id=device["id"], enabled=True, interval_minutes=60))
    db.commit()
    sched.sync_jobs()
    first = _job(device["id"]).next_run_time
    sched.sync_jobs()
    assert _job(device["id"]).next_run_time == first


def test_non_runner_skips_scheduled_backup(client, monkeypatch, db, device):
    called = []
    monkeypatch.setattr(sched, "is_leader", lambda: False)
    monkeypatch.setattr(sched, "run_backup", lambda *a: called.append(a))
    sched.execute_scheduled_backup(device["id"])
    assert called == []


def test_schedule_api(client, super_admin, device):
    r = client.put(f"/api/schedules/{device['id']}", headers=super_admin,
                   json={"enabled": True, "interval_minutes": 360, "retention_count": 10})
    assert r.status_code == 200 and r.json()["next_run"]
    r = client.put(f"/api/schedules/{device['id']}", headers=super_admin, json={"interval_minutes": 5})
    assert r.status_code == 422          # below the 15 minute minimum
