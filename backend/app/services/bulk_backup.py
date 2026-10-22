# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Back up every enabled device at once, on the server.

The run happens in a background thread with BACKUP_WORKERS SSH sessions in
parallel, so it does not depend on a browser tab staying open. Progress is kept
in the database, so any worker process can report it, and a lock file makes
sure only one run happens at a time across all worker processes.
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app.core.config import BASE_DIR, settings
from app.core.timeutil import utcnow
from app.services import settings_store as store

logger = logging.getLogger("ncm.bulk_backup")

K_STATE = "backup.run_all"
LOCK_FILE = BASE_DIR / ".backup-all.lock"
MAX_FAILED_LISTED = 50
_thread_lock = threading.Lock()      # fallback where flock does not exist (Windows)


def workers() -> int:
    return max(1, min(int(settings.backup_workers or 10), 50))


def _try_lock():
    """An exclusive lock held for the whole run; released automatically if the process dies."""
    try:
        import fcntl
    except ImportError:
        return _thread_lock if _thread_lock.acquire(blocking=False) else None
    fh = open(LOCK_FILE, "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _release(lock):
    if lock is _thread_lock:
        lock.release()
    else:
        lock.close()


def state(db) -> dict:
    s = store.get_json(db, K_STATE, {}) or {}
    if s.get("running"):
        probe = _try_lock()
        if probe is not None:            # nobody holds the lock: the run was cut off by a restart
            _release(probe)
            s.update(running=False, interrupted=True)
    return s


def start(user) -> tuple[bool, dict]:
    """Start a run in the background. Returns (started, state)."""
    from app.core.database import SessionLocal
    from app.models.device import Device

    lock = _try_lock()
    db = SessionLocal()
    try:
        if lock is None:
            return False, state(db)
        ids = [d.id for d in db.query(Device).filter(Device.enabled.is_(True)).order_by(Device.hostname).all()]
        s = {"running": True, "total": len(ids), "done": 0, "ok": 0, "failed": 0, "changed": 0,
             "failed_hosts": [], "workers": workers(), "started_by": getattr(user, "username", None),
             "started_at": utcnow().isoformat(), "finished_at": None}
        store.set_json(db, K_STATE, s)
    except Exception:
        if lock is not None:
            _release(lock)
        raise
    finally:
        db.close()
    threading.Thread(target=_run, args=(ids, s, lock), name="ncm-backup-all", daemon=True).start()
    return True, s


def _run(ids: list[int], s: dict, lock):
    from app.core.database import SessionLocal
    from app.services.audit import record_audit

    progress = threading.Lock()

    def save():
        db = SessionLocal()
        try:
            store.set_json(db, K_STATE, s)
        finally:
            db.close()

    def one(device_id: int):
        from app.models.device import Device
        from app.services.backup import run_backup
        db = SessionLocal()
        result, host = None, f"device-{device_id}"
        try:
            device = db.get(Device, device_id)
            if device is not None and device.enabled:
                host = device.hostname
                result = run_backup(device, db)
        except Exception as exc:          # run_backup records its own failures; this is a safety net
            logger.exception("Backup of device %s failed", device_id)
            result = {"success": False, "error": str(exc)}
        finally:
            db.close()
        with progress:
            s["done"] += 1
            if result is None:
                pass                         # deleted or disabled since the run started
            elif result.get("success"):
                s["ok"] += 1
                s["changed"] += 1 if result.get("changed") else 0
            else:
                s["failed"] += 1
                if len(s["failed_hosts"]) < MAX_FAILED_LISTED:
                    s["failed_hosts"].append({"host": host, "error": (result.get("error") or "")[:200]})
            save()

    try:
        with ThreadPoolExecutor(max_workers=s["workers"], thread_name_prefix="ncm-backup") as pool:
            list(pool.map(one, ids))
    finally:
        s.update(running=False, finished_at=utcnow().isoformat())
        save()
        _release(lock)
        db = SessionLocal()
        try:
            record_audit(db, None, "BACKUP_RUN_ALL_DONE", "backup", "all",
                         status="SUCCESS" if not s["failed"] else "FAILED",
                         details=f"started_by={s['started_by']};devices={s['total']};ok={s['ok']};"
                                 f"failed={s['failed']};changed={s['changed']}")
        finally:
            db.close()
        logger.info("Backup of all devices finished: %s ok, %s failed, %s changed", s["ok"], s["failed"], s["changed"])
