# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Background housekeeping tasks."""
import logging
import threading
from pathlib import Path

logger = logging.getLogger("ncm.maintenance")
FINGERPRINT_VERSION = "2"   # v2 = fingerprint ignores timestamp / header lines


def backfill_fingerprints(batch: int = 500):
    """Fill size and config fingerprint for older backups.

    Runs for backups without a fingerprint, and once for every backup when the
    fingerprint method changes (stored in app_settings).
    """
    from app.core.database import SessionLocal
    from app.models.backup import Backup
    from app.services import settings_store as store
    from app.services.diff import config_fingerprint

    db = SessionLocal()
    done = 0
    try:
        recompute_all = store.get(db, "maint.fingerprint_version") != FINGERPRINT_VERSION
        last_id = 0
        while True:
            q = db.query(Backup).filter(Backup.status == "SUCCESS", Backup.id > last_id)
            if not recompute_all:
                q = q.filter(Backup.sha256.is_(None))
            rows = q.order_by(Backup.id.asc()).limit(batch).all()
            if not rows:
                break
            for b in rows:
                last_id = b.id
                try:
                    data = Path(b.file_path).read_bytes()
                except (OSError, TypeError):
                    continue
                b.sha256 = config_fingerprint(data.decode("utf-8", errors="replace"))
                b.size_bytes = len(data)
                done += 1
            db.commit()
        store.set(db, "maint.fingerprint_version", FINGERPRINT_VERSION)
        if done:
            logger.info("Updated config fingerprints for %s backups", done)
    except Exception:
        logger.exception("Fingerprint backfill failed")
    finally:
        db.close()


FAILED_KEEP_DAYS = 30        # failed backup records older than this are removed
RUNNING_STALE_HOURS = 6      # a backup still "RUNNING" after this was cut off (restart, crash)


def daily_cleanup():
    """Remove old failed backup records and close backups left RUNNING by a restart."""
    from datetime import timedelta
    from app.core.database import SessionLocal
    from app.core.timeutil import utcnow
    from app.models.backup import Backup
    from app.services.scheduler import is_leader
    if not is_leader():
        return
    db = SessionLocal()
    try:
        now = utcnow()
        stale = (db.query(Backup).filter(Backup.status == "RUNNING",
                                         Backup.created_at < now - timedelta(hours=RUNNING_STALE_HOURS))
                 .update({Backup.status: "FAILED", Backup.error: "Interrupted (NCM was restarted during the backup)"},
                         synchronize_session=False))
        removed = (db.query(Backup).filter(Backup.status == "FAILED",
                                           Backup.created_at < now - timedelta(days=FAILED_KEEP_DAYS))
                   .delete(synchronize_session=False))
        db.commit()
        if stale or removed:
            logger.info("Cleanup: %s interrupted backups closed, %s old failed records removed", stale, removed)
    except Exception:
        db.rollback()
        logger.exception("Daily cleanup failed")
    finally:
        db.close()


def start_background_tasks():
    threading.Thread(target=backfill_fingerprints, name="ncm-fingerprints", daemon=True).start()
