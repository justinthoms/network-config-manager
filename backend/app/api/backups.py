# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import settings
from app.core.database import get_db
from app.models.device import Device
from app.models.backup import Backup
from app.services.backup import run_backup
from app.services.audit import record_audit
from app.services.diff import compare, quick_stats

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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _serialize(b: Backup, hostname: str):
    return {
        "id": b.id,
        "device_id": b.device_id,
        "hostname": hostname,
        "status": b.status,
        "file_path": b.file_path,
        "file_name": Path(b.file_path).name if b.file_path else None,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "error": b.error,
        "gdrive_file_id": b.gdrive_file_id,
        "gdrive_synced_at": b.gdrive_synced_at.isoformat() if b.gdrive_synced_at else None,
        "onedrive_item_id": b.onedrive_item_id,
        "onedrive_synced_at": b.onedrive_synced_at.isoformat() if b.onedrive_synced_at else None,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("/")
def list_backups(device_id: int | None = None, status: str | None = None, limit: int = 100,
                 db: Session = Depends(get_db), user=Depends(require_permission("backups.view"))):
    limit = max(1, min(limit, 1000))
    query = db.query(Backup)
    if device_id is not None:
        query = query.filter(Backup.device_id == device_id)
    if status:
        query = query.filter(Backup.status == status.upper())

    rows = query.order_by(Backup.id.desc()).limit(limit).all()
    device_ids = {x.device_id for x in rows}
    devices = {
        x.id: x.hostname
        for x in db.query(Device).filter(Device.id.in_(device_ids)).all()
    } if device_ids else {}
    return [_serialize(b, devices.get(b.device_id, f"Device {b.device_id}")) for b in rows]


@router.post("/run-all", status_code=202)
def backup_all(response: Response, db: Session = Depends(get_db), user=Depends(require_permission("backups.run"))):
    """Back up every enabled device in the background; poll GET /run-all for progress."""
    from app.services import bulk_backup
    started, state = bulk_backup.start(user)
    if not started:
        response.status_code = 409
        return {**state, "detail": "A backup of all devices is already running"}
    record_audit(db, user, "BACKUP_RUN_ALL", "backup", "all", details=f"devices={state['total']};workers={state['workers']}")
    return state


@router.get("/run-all")
def backup_all_state(db: Session = Depends(get_db), user=Depends(require_permission("backups.view"))):
    from app.services import bulk_backup
    return bulk_backup.state(db)


@router.get("/diff")
def backup_diff(backup_a: int, backup_b: int,
                ignore_whitespace: bool = False,
                ignore_volatile: bool = True,
                cross_device: bool = False,
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
    if a.device_id != b.device_id and not cross_device:
        raise HTTPException(400, "Backups belong to different devices. Turn on 'Compare across devices' to continue.")

    path_a = _safe_backup_path(a.file_path)
    path_b = _safe_backup_path(b.file_path)
    hosts = {d.id: d.hostname for d in db.query(Device).filter(Device.id.in_({a.device_id, b.device_id})).all()}

    result = compare(
        _read(path_a), _read(path_b),
        ignore_whitespace=ignore_whitespace, ignore_volatile=ignore_volatile,
        label_a=f"#{a.id} {hosts.get(a.device_id, '')} ({path_a.name})",
        label_b=f"#{b.id} {hosts.get(b.device_id, '')} ({path_b.name})",
    )
    result.update({
        "backup_a": _serialize(a, hosts.get(a.device_id, f"Device {a.device_id}")),
        "backup_b": _serialize(b, hosts.get(b.device_id, f"Device {b.device_id}")),
        "device_id": a.device_id,
    })
    return result


@router.get("/changes")
def change_timeline(device_id: int, limit: int = 30, ignore_volatile: bool = True,
                    db: Session = Depends(get_db), user=Depends(require_permission("backups.diff"))):
    """Each successful backup with how much it changed versus the backup before it."""
    limit = max(2, min(limit, 100))
    rows = (
        db.query(Backup)
        .filter(Backup.device_id == device_id, Backup.status == "SUCCESS")
        .order_by(Backup.id.desc())
        .limit(limit + 1)
        .all()
    )[::-1]
    out, prev_text, prev = [], None, None
    for b in rows:
        try:
            text = _read(_safe_backup_path(b.file_path))
        except HTTPException:
            text = None
        entry = {"id": b.id, "created_at": b.created_at.isoformat() if b.created_at else None,
                 "size_bytes": b.size_bytes, "previous_id": prev.id if prev else None,
                 "added": None, "removed": None, "changed": None, "missing": text is None}
        if text is not None and prev_text is not None:
            added, removed = quick_stats(prev_text, text, ignore_volatile)
            entry.update(added=added, removed=removed, changed=bool(added or removed))
        out.append(entry)
        if text is not None:
            prev_text, prev = text, b
    if len(out) > limit:
        out = out[1:]
    return out[::-1]


@router.get("/{backup_id}/view", response_class=PlainTextResponse)
def view_backup(backup_id: int, db: Session = Depends(get_db),
                user=Depends(require_permission("backups.view"))):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(404, "Backup not found")
    if backup.status != "SUCCESS":
        raise HTTPException(404, "Only successful backups can be viewed")
    return _read(_safe_backup_path(backup.file_path))


@router.get("/{backup_id}/download")
def download_backup(backup_id: int, db: Session = Depends(get_db),
                    user=Depends(require_permission("backups.download"))):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(404, "Backup not found")
    if backup.status != "SUCCESS":
        raise HTTPException(404, "Backup file is not available")
    device = db.query(Device).filter(Device.id == backup.device_id).first()
    path = _safe_backup_path(backup.file_path)
    name = f"{device.hostname}_{path.name}" if device else path.name
    return FileResponse(path=str(path), filename=name, media_type="text/plain")


@router.delete("/{backup_id}")
def delete_backup(backup_id: int, db: Session = Depends(get_db),
                  user=Depends(require_permission("backups.delete"))):
    backup = db.query(Backup).filter(Backup.id == backup_id).first()
    if not backup:
        raise HTTPException(404, "Backup not found")
    if backup.file_path:
        try:
            path = _safe_backup_path(backup.file_path)
            path.unlink()
        except HTTPException:
            pass
    db.delete(backup)
    db.commit()
    record_audit(db, user, "BACKUP_DELETE", "backup", backup_id)
    return {"success": True}


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
