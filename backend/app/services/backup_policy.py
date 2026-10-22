# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Automatic backup for all devices.

When the policy is on, every device gets a backup schedule (default: every
24 hours, starting at 01:00 server time). New devices, added by hand or by
CSV import, get one automatically. Runs are spread over 15 minutes so many
devices do not all connect at the same second.

On the first start after upgrading, the policy is switched on once
(every 24 h). After that, the admin's choice on the Schedules page is kept.
"""
from __future__ import annotations

import logging
import re

from app.services import settings_store as store
from app.core.timeutil import utcnow

logger = logging.getLogger("ncm.policy")

K_ON = "backup.auto_all"
K_INTERVAL = "backup.auto_interval"
K_START = "backup.auto_start"
K_RETENTION = "backup.auto_retention"
K_INIT = "backup.auto_initialized"

DEFAULT_INTERVAL = 1440
DEFAULT_START = "01:00"
DEFAULT_RETENTION = 30
SPREAD_SECONDS = 900


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def get(db) -> dict:
    start = store.get(db, K_START, DEFAULT_START)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", start or ""):
        start = DEFAULT_START
    return {
        "enabled": store.get(db, K_ON, "0") == "1",
        "interval_minutes": _int(store.get(db, K_INTERVAL), DEFAULT_INTERVAL, 15, 525600),
        "start_time": start,
        "retention_count": _int(store.get(db, K_RETENTION), DEFAULT_RETENTION, 1, 10000),
    }


def save(db, enabled: bool, interval: int, start_time: str, retention: int):
    store.set(db, K_ON, "1" if enabled else "0", commit=False)
    store.set(db, K_INTERVAL, interval, commit=False)
    store.set(db, K_START, start_time, commit=False)
    store.set(db, K_RETENTION, retention, commit=False)
    db.commit()


def apply(db, overwrite: bool = False) -> dict:
    """Give every device a schedule from the policy.

    overwrite=False: create missing schedules and switch paused ones on; custom
    frequencies already set on a device are kept.
    overwrite=True: also set every schedule to the policy frequency and retention.
    """
    from app.models.device import Device
    from app.models.schedule import BackupSchedule
    from app.services.scheduler import schedule_device

    p = get(db)
    existing = {s.device_id: s for s in db.query(BackupSchedule).all()}
    counts = {"created": 0, "enabled": 0, "updated": 0, "unchanged": 0}
    touched = []
    for device in db.query(Device).all():
        s = existing.get(device.id)
        if s is None:
            s = BackupSchedule(device_id=device.id, enabled=True, interval_minutes=p["interval_minutes"],
                               retention_count=p["retention_count"])
            db.add(s)
            counts["created"] += 1
        elif overwrite and (not s.enabled or s.interval_minutes != p["interval_minutes"]
                            or s.retention_count != p["retention_count"]):
            s.enabled, s.interval_minutes, s.retention_count = True, p["interval_minutes"], p["retention_count"]
            s.updated_at = utcnow()
            counts["updated"] += 1
        elif not s.enabled:
            s.enabled = True
            s.updated_at = utcnow()
            counts["enabled"] += 1
        else:
            counts["unchanged"] += 1
            continue
        touched.append(s)
    db.commit()
    for s in touched:
        db.refresh(s)
        schedule_device(s)
    return counts


def on_device_created(db, device) -> bool:
    """Called after a device is added. Returns True if a schedule was created."""
    from app.models.schedule import BackupSchedule
    from app.services.scheduler import schedule_device
    if not get(db)["enabled"]:
        return False
    if db.query(BackupSchedule).filter(BackupSchedule.device_id == device.id).first():
        return False
    p = get(db)
    s = BackupSchedule(device_id=device.id, enabled=True, interval_minutes=p["interval_minutes"],
                       retention_count=p["retention_count"])
    db.add(s)
    db.commit()
    db.refresh(s)
    schedule_device(s)
    return True


def ensure_initialized(db):
    """First start after upgrade: switch the policy on (every 24 h) and apply it once."""
    if store.get(db, K_INIT) == "1":
        return
    save(db, True, DEFAULT_INTERVAL, DEFAULT_START, DEFAULT_RETENTION)
    store.set(db, K_INIT, "1")
    counts = apply(db, overwrite=False)
    logger.info("Automatic backup for all devices switched on (every 24 h): %s", counts)


def start_time(db=None) -> str:
    own = db is None
    if own:
        from app.core.database import SessionLocal
        db = SessionLocal()
    try:
        return get(db)["start_time"]
    finally:
        if own:
            db.close()
