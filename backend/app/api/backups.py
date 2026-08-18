from difflib import unified_diff
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_permission
from app.core.config import settings
from app.core.database import get_db
from app.models.device import Device
from app.models.backup import Backup
from app.services.backup import run_backup
from app.services.audit import record_audit

router = APIRouter()


def _safe_backup_path(file_path: str | None) -> Path:
    if not file_path:
        raise HTTPException(404, "Backup file is not available")

    root = Path(settings.backup_root).resolve()
    path = Path(file_path).resolve()

    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Invalid backup file path")

    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Backup file is missing")

    return path


@router.get("/")
def list_backups(device_id: int | None = None, limit: int = 100,
                 db: Session = Depends(get_db), user=Depends(require_permission("backups.view"))):
    limit = max(1, min(limit, 500))
    query = db.query(Backup)

    if device_id is not None:
        query = query.filter(Backup.device_id == device_id)

    rows = query.order_by(Backup.id.desc()).limit(limit).all()

    device_ids = {x.device_id for x in rows}
    devices = {
        x.id: x.hostname
        for x in db.query(Device).filter(Device.id.in_(device_ids)).all()
    } if device_ids else {}

    return [
        {
            "id": b.id,
            "device_id": b.device_id,
            "hostname": devices.get(b.device_id, f"Device {b.device_id}"),
            "status": b.status,
            "file_path": b.file_path,
            "error": b.error,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in rows
    ]


@router.get("/diff")
def backup_diff(backup_a: int, backup_b: int,
                db: Session = Depends(get_db),
                user=Depends(require_permission("backups.diff"))):
    if backup_a == backup_b:
        raise HTTPException(400, "Select two different backups")

    a = db.query(Backup).filter(Backup.id == backup_a).first()
    b = db.query(Backup).filter(Backup.id == backup_b).first()

    if not a or not b:
        raise HTTPException(404, "One or both backups were not found")
    if a.status != "SUCCESS" or b.status != "SUCCESS":
        raise HTTPException(400, "Only successful backups can be compared")
    if a.device_id != b.device_id:
        raise HTTPException(400, "Backups must belong to the same device")

    path_a = _safe_backup_path(a.file_path)
    path_b = _safe_backup_path(b.file_path)

    text_a = path_a.read_text(encoding="utf-8", errors="replace").splitlines()
    text_b = path_b.read_text(encoding="utf-8", errors="replace").splitlines()

    diff = list(unified_diff(
        text_a,
        text_b,
        fromfile=f"Backup #{a.id} ({path_a.name})",
        tofile=f"Backup #{b.id} ({path_b.name})",
        lineterm="",
    ))

    return {
        "backup_a": a.id,
        "backup_b": b.id,
        "device_id": a.device_id,
        "lines": diff,
        "changed": bool(diff),
    }


@router.get("/{backup_id}/view", response_class=PlainTextResponse)
def view_backup(backup_id: int, db: Session = Depends(get_db),
                user=Depends(require_permission("backups.view"))):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(404, "Backup not found")
    if backup.status != "SUCCESS":
        raise HTTPException(404, "Only successful backups can be viewed")

    path = _safe_backup_path(backup.file_path)
    return path.read_text(encoding="utf-8", errors="replace")


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db),
                    user=Depends(require_permission("backups.download"))):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(404, "Backup not found")
    if backup.status != "SUCCESS":
        raise HTTPException(404, "Backup file is not available")

    path = _safe_backup_path(backup.file_path)
    return FileResponse(path=str(path), filename=path.name, media_type="text/plain")


@router.post("/{device_id}/run")
def backup_now(device_id: int, db: Session = Depends(get_db),
               user=Depends(require_permission("backups.run"))):
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(404, "Device not found")
    if not device.enabled:
        raise HTTPException(400, "Device is disabled")

    result = run_backup(device, db)
    record_audit(db, user, "BACKUP_RUN", "device", device.id,
                 status="SUCCESS" if result.get("success") else "FAILED",
                 details=result.get("error") or result.get("file_path"))
    return result
