from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_permission
from app.core.database import get_db
from app.models.device import Device
from app.models.schedule import BackupSchedule
from app.services.scheduler import scheduler, schedule_device, remove_device_schedule
from app.services.audit import record_audit

router = APIRouter()


class ScheduleIn(BaseModel):
    enabled: bool = True
    interval_minutes: int = Field(default=360, ge=15, le=525600)
    retry_count: int = Field(default=2, ge=0, le=10)
    retry_delay_seconds: int = Field(default=30, ge=0, le=3600)
    retention_count: int = Field(default=30, ge=1, le=10000)


def _serialize(item: BackupSchedule):
    job = scheduler.get_job(f"backup-device-{item.device_id}")
    return {
        "id": item.id,
        "device_id": item.device_id,
        "enabled": item.enabled,
        "interval_minutes": item.interval_minutes,
        "retry_count": item.retry_count,
        "retry_delay_seconds": item.retry_delay_seconds,
        "retention_count": item.retention_count,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("/")
def list_schedules(device_id: int | None = None, db: Session = Depends(get_db),
                   user=Depends(require_permission("schedules.view"))):
    query = db.query(BackupSchedule)
    if device_id is not None:
        query = query.filter(BackupSchedule.device_id == device_id)
    return [_serialize(x) for x in query.order_by(BackupSchedule.device_id).all()]


class PolicyIn(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=1440, ge=15, le=525600)
    start_time: str = Field(default="01:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    retention_count: int = Field(default=30, ge=1, le=10000)
    overwrite: bool = False        # also change devices that already have their own schedule


def _policy_out(db):
    from app.services import backup_policy
    p = backup_policy.get(db)
    total = db.query(Device).count()
    active = db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).count()
    custom = db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True),
                                             BackupSchedule.interval_minutes != p["interval_minutes"]).count()
    return {**p, "devices": total, "scheduled": active, "unscheduled": max(0, total - active),
            "custom": custom, "server_time": datetime.now().strftime("%H:%M")}


@router.get("/policy")
def get_policy(db: Session = Depends(get_db), user=Depends(require_permission("schedules.view"))):
    return _policy_out(db)


@router.put("/policy")
def put_policy(data: PolicyIn, db: Session = Depends(get_db), user=Depends(require_permission("schedules.edit"))):
    from app.services import backup_policy
    old = backup_policy.get(db)
    backup_policy.save(db, data.enabled, data.interval_minutes, data.start_time, data.retention_count)
    counts = {}
    if data.enabled:
        counts = backup_policy.apply(db, overwrite=data.overwrite)
    if old["start_time"] != data.start_time:
        # re-plan daily schedules so they move to the new start time
        from app.services.scheduler import schedule_device
        for s in db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).all():
            if s.interval_minutes % 1440 == 0:
                schedule_device(s)
    record_audit(db, user, "BACKUP_POLICY", "schedule", "all",
                 details=f"enabled={data.enabled};interval={data.interval_minutes};start={data.start_time};"
                         f"overwrite={data.overwrite};" + ";".join(f"{k}={v}" for k, v in counts.items()))
    return {**_policy_out(db), "applied": counts}


@router.get("/{device_id}")
def get_schedule(device_id: int, db: Session = Depends(get_db),
                 user=Depends(require_permission("schedules.view"))):
    item = db.query(BackupSchedule).filter(
        BackupSchedule.device_id == device_id
    ).first()
    if not item:
        return {
            "device_id": device_id,
            "enabled": False,
            "interval_minutes": 360,
            "retry_count": 2,
            "retry_delay_seconds": 30,
            "retention_count": 30,
            "next_run": None,
        }
    return _serialize(item)


@router.put("/{device_id}")
def upsert_schedule(device_id: int, data: ScheduleIn,
                    db: Session = Depends(get_db),
                    user=Depends(require_permission("schedules.edit"))):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(404, "Device not found")

    item = db.query(BackupSchedule).filter(
        BackupSchedule.device_id == device_id
    ).first()

    if not item:
        item = BackupSchedule(device_id=device_id)
        db.add(item)

    item.enabled = data.enabled
    item.interval_minutes = data.interval_minutes
    item.retry_count = data.retry_count
    item.retry_delay_seconds = data.retry_delay_seconds
    item.retention_count = data.retention_count
    item.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(item)

    if item.enabled:
        schedule_device(item)
    else:
        remove_device_schedule(device_id)

    record_audit(db, user, "SCHEDULE_UPDATE", "device", device_id, details=f"enabled={item.enabled};interval={item.interval_minutes}")
    return _serialize(item)


@router.delete("/{device_id}")
def delete_schedule(device_id: int, db: Session = Depends(get_db),
                    user=Depends(require_permission("schedules.edit"))):
    item = db.query(BackupSchedule).filter(
        BackupSchedule.device_id == device_id
    ).first()
    remove_device_schedule(device_id)

    if item:
        db.delete(item)
        db.commit()

    record_audit(db, user, "SCHEDULE_DELETE", "device", device_id)
    return {"success": True, "message": "Backup schedule removed"}
