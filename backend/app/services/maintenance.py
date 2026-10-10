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


def start_background_tasks():
    threading.Thread(target=backfill_fingerprints, name="ncm-fingerprints", daemon=True).start()
