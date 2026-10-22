import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import BASE_DIR
from app.core.database import SessionLocal
from app.models.device import Device
from app.models.backup import Backup
from app.models.schedule import BackupSchedule
from app.services.backup import run_backup

logger = logging.getLogger("ncm.scheduler")

scheduler = BackgroundScheduler(
    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300}
)

# ───────────────────────── one runner per server ─────────────────────────
# Every worker process (uvicorn --workers N) keeps its own scheduler so the UI can
# show next-run times, but only the process holding this lock runs backups and
# health checks. If that process stops, another takes over at its next sync.
LOCK_FILE = BASE_DIR / ".scheduler.lock"
SYNC_SECONDS = 60
_leader: dict = {"fd": None}
# device_id -> settings the job was planned with, so a sync only re-plans what changed
_applied: dict[int, tuple] = {}


def is_leader() -> bool:
    if _leader["fd"] is not None:
        return True
    try:
        import fcntl
    except ImportError:          # no flock (Windows): a single process is assumed
        _leader["fd"] = True
        return True
    fd = None
    try:
        fd = open(LOCK_FILE, "a")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if fd:
            fd.close()
        return False
    _leader["fd"] = fd
    logger.info("This process runs scheduled backups and health checks")
    return True


def _cleanup_retention(db, device_id: int, keep: int):
    if keep < 1:
        return

    rows = (
        db.query(Backup)
        .filter(Backup.device_id == device_id, Backup.status == "SUCCESS")
        .order_by(Backup.id.desc())
        .all()
    )
    for old in rows[keep:]:
        if old.file_path:
            try:
                path = Path(old.file_path)
                if path.exists() and path.is_file():
                    path.unlink()
            except Exception:
                logger.exception("Could not delete old backup file %s", old.file_path)
        db.delete(old)
    db.commit()


def execute_scheduled_backup(device_id: int):
    if not is_leader():
        return
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        schedule = db.query(BackupSchedule).filter(
            BackupSchedule.device_id == device_id
        ).first()

        if not device or not schedule or not schedule.enabled or not device.enabled:
            return

        last_result = None
        attempts = max(1, schedule.retry_count + 1)

        for attempt in range(attempts):
            last_result = run_backup(device, db)
            if last_result.get("success"):
                _cleanup_retention(db, device.id, schedule.retention_count)
                return

            if attempt < attempts - 1:
                time.sleep(max(0, schedule.retry_delay_seconds))

        logger.warning(
            "Scheduled backup failed for device %s after %s attempts: %s",
            device.hostname, attempts, last_result,
        )
    except Exception:
        logger.exception("Scheduled backup job failed for device %s", device_id)
    finally:
        db.close()


def first_run_at(device_id: int, interval_minutes: int, now: datetime | None = None) -> datetime:
    """When a schedule should first run.

    Daily (and multi-day) schedules start at the policy start time (default
    01:00 server time). Others start one interval from now. Each device gets a
    fixed offset of up to 15 minutes so devices do not all connect at once.
    """
    from app.services.backup_policy import start_time, SPREAD_SECONDS
    now = now or datetime.now()
    spread = min(SPREAD_SECONDS, max(60, interval_minutes * 60 // 4))
    offset = timedelta(seconds=(device_id * 37) % spread)
    if interval_minutes % 1440 == 0:
        hh, mm = map(int, start_time().split(":"))
        first = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if first + offset <= now:
            first += timedelta(days=1)
        return first + offset
    return now + timedelta(minutes=interval_minutes) + offset


def _signature(schedule: BackupSchedule, start: str) -> tuple:
    return (schedule.interval_minutes, start if schedule.interval_minutes % 1440 == 0 else None)


def schedule_device(schedule: BackupSchedule):
    from app.services.backup_policy import start_time
    job_id = f"backup-device-{schedule.device_id}"

    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    _applied.pop(schedule.device_id, None)

    if schedule.enabled:
        _applied[schedule.device_id] = _signature(schedule, start_time())
        scheduler.add_job(
            execute_scheduled_backup,
            trigger=IntervalTrigger(minutes=schedule.interval_minutes,
                                    start_date=first_run_at(schedule.device_id, schedule.interval_minutes)),
            args=[schedule.device_id],
            id=job_id,
            replace_existing=True,
        )


def remove_device_schedule(device_id: int):
    job_id = f"backup-device-{device_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
    _applied.pop(device_id, None)


def sync_jobs():
    """Bring this process's jobs in line with the database.

    Schedules changed through another worker process are picked up here, and a
    process that is not the runner tries to take over if the runner has stopped.
    """
    from app.services.backup_policy import start_time
    is_leader()
    db = SessionLocal()
    try:
        start = start_time(db)
        rows = {s.device_id: s for s in db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).all()}
        for device_id in list(_applied):
            if device_id not in rows:
                remove_device_schedule(device_id)
        for device_id, s in rows.items():
            if (_applied.get(device_id) != _signature(s, start)
                    or not scheduler.get_job(f"backup-device-{device_id}")):
                schedule_device(s)
        from app.services.health import schedule_job
        schedule_job(db, only_if_changed=True)
    except Exception:
        logger.exception("Could not sync scheduled jobs")
    finally:
        db.close()


def start_scheduler():
    if scheduler.running:
        return

    db = SessionLocal()
    try:
        try:
            from app.services.backup_policy import ensure_initialized
            ensure_initialized(db)
        except Exception:
            logger.exception("Could not apply the automatic backup policy")
        schedules = db.query(BackupSchedule).filter(BackupSchedule.enabled == True).all()
        for item in schedules:
            schedule_device(item)
    finally:
        db.close()

    scheduler.start()
    scheduler.add_job(sync_jobs, trigger=IntervalTrigger(seconds=SYNC_SECONDS), id="ncm-sync", replace_existing=True)
    from app.services.maintenance import daily_cleanup
    scheduler.add_job(daily_cleanup, trigger=IntervalTrigger(hours=24, start_date=datetime.now() + timedelta(minutes=5)),
                      id="ncm-cleanup", replace_existing=True)
    logger.info("Backup scheduler started (%s)", "runs jobs" if is_leader() else "jobs run in another worker process")
    try:
        from app.services.health import schedule_job
        schedule_job()
    except Exception:
        logger.exception("Could not schedule device health checks")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Backup scheduler stopped")
