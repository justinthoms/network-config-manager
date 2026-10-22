# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.backup import Backup
from app.models.device import Device
from app.models.user import User
from app.services import gdrive
from app.services import settings_store as store
from app.services.audit import record_audit

router = APIRouter()


class CredentialsIn(BaseModel):
    client_id: str = Field(min_length=10, max_length=300)
    client_secret: str | None = Field(default=None, max_length=300)


class SettingsIn(BaseModel):
    auto_sync: bool | None = None
    root_folder: str | None = Field(default=None, min_length=1, max_length=100)


def _drive_call(db, fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except gdrive.DriveError as exc:
        gdrive.record_error(db, str(exc))
        raise HTTPException(400, str(exc))


@router.get("/gdrive/status")
def gdrive_status(db: Session = Depends(get_db), user=Depends(require_permission("cloud.view"))):
    data = gdrive.status(db)
    data["synced_backups"] = db.query(Backup).filter(Backup.gdrive_file_id.isnot(None)).count()
    data["pending_backups"] = db.query(Backup).filter(
        Backup.status == "SUCCESS", Backup.gdrive_file_id.is_(None)).count()
    return data


@router.put("/gdrive/credentials")
def gdrive_credentials(data: CredentialsIn, db: Session = Depends(get_db),
                       user=Depends(require_permission("cloud.manage"))):
    if user.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can change OAuth client credentials")
    if gdrive.credentials_source(db) == "env":
        raise HTTPException(400, "Credentials are set in the .env file. Edit GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET there.")
    if not data.client_secret and not store.get(db, gdrive.K_CLIENT_SECRET):
        raise HTTPException(400, "Client secret is required")
    gdrive.save_client_credentials(db, data.client_id, data.client_secret)
    record_audit(db, user, "GDRIVE_CREDENTIALS", "cloud", "gdrive")
    return gdrive.status(db)


@router.get("/gdrive/auth-url")
def gdrive_auth_url(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    return {"url": _drive_call(db, gdrive.build_auth_url, db, user.id)}


@router.get("/gdrive/callback", include_in_schema=False)
def gdrive_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    if error or not code or not state:
        return RedirectResponse(f"/?gdrive=error&reason={error or 'cancelled'}#cloud")
    try:
        user_id = gdrive.verify_state(state)
        user = db.get(User, user_id)
        if not user or not user.is_active:
            raise gdrive.DriveError("User not allowed")
        gdrive.exchange_code(db, code)
        record_audit(db, user, "GDRIVE_CONNECT", "cloud", "gdrive",
                     details=store.get(db, gdrive.K_EMAIL))
        return RedirectResponse("/?gdrive=connected#cloud")
    except gdrive.DriveError as exc:
        gdrive.record_error(db, str(exc))
        return RedirectResponse("/?gdrive=error&reason=token#cloud")


@router.post("/gdrive/disconnect")
def gdrive_disconnect(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    gdrive.disconnect(db)
    record_audit(db, user, "GDRIVE_DISCONNECT", "cloud", "gdrive")
    return gdrive.status(db)


@router.put("/gdrive/settings")
def gdrive_settings(data: SettingsIn, db: Session = Depends(get_db),
                    user=Depends(require_permission("cloud.manage"))):
    if data.auto_sync is not None:
        store.set(db, gdrive.K_AUTO, "1" if data.auto_sync else "0", commit=False)
    if data.root_folder is not None:
        name = data.root_folder.strip().replace("/", "-")
        if name != gdrive.root_folder_name(db):
            store.set(db, gdrive.K_ROOT_NAME, name, commit=False)
            store.set(db, gdrive.K_FOLDERS, None, commit=False)
    db.commit()
    record_audit(db, user, "GDRIVE_SETTINGS", "cloud", "gdrive",
                 details=f"auto_sync={data.auto_sync};root={data.root_folder}")
    return gdrive.status(db)


@router.post("/gdrive/upload/{backup_id}")
def gdrive_upload(backup_id: int, db: Session = Depends(get_db),
                  user=Depends(require_permission("cloud.sync"))):
    backup = db.get(Backup, backup_id)
    if not backup or backup.status != "SUCCESS":
        raise HTTPException(404, "Successful backup not found")
    device = db.get(Device, backup.device_id)
    result = _drive_call(db, gdrive.upload_backup, db, backup,
                         device.hostname if device else f"device-{backup.device_id}")
    record_audit(db, user, "GDRIVE_UPLOAD", "backup", backup_id, details=result.get("name"))
    return {"success": True, "file": result}


@router.post("/gdrive/sync")
def gdrive_sync(limit: int = 50, db: Session = Depends(get_db),
                user=Depends(require_permission("cloud.sync"))):
    """Upload successful backups that are not in Drive yet (oldest first)."""
    if not gdrive.is_connected(db):
        raise HTTPException(400, "Google Drive is not connected")
    limit = max(1, min(limit, 500))
    pending = (db.query(Backup)
               .filter(Backup.status == "SUCCESS", Backup.gdrive_file_id.is_(None))
               .order_by(Backup.id.asc()).limit(limit).all())
    hosts = {d.id: d.hostname for d in db.query(Device).all()}
    uploaded, failed, errors = 0, 0, []
    for b in pending:
        try:
            gdrive.upload_backup(db, b, hosts.get(b.device_id, f"device-{b.device_id}"))
            uploaded += 1
        except Exception as exc:
            failed += 1
            errors.append(f"#{b.id}: {exc}")
            if isinstance(exc, gdrive.DriveError) and "connected" in str(exc).lower():
                break
    if errors:
        gdrive.record_error(db, errors[-1])
    remaining = db.query(Backup).filter(Backup.status == "SUCCESS", Backup.gdrive_file_id.is_(None)).count()
    record_audit(db, user, "GDRIVE_SYNC", "cloud", "gdrive",
                 status="SUCCESS" if not failed else "FAILED",
                 details=f"uploaded={uploaded};failed={failed}")
    return {"uploaded": uploaded, "failed": failed, "remaining": remaining, "errors": errors[:10]}


@router.get("/gdrive/files")
def gdrive_files(limit: int = 100, db: Session = Depends(get_db),
                 user=Depends(require_permission("cloud.view"))):
    return _drive_call(db, gdrive.list_files, db, limit)


@router.get("/gdrive/files/{file_id}/download")
def gdrive_download(file_id: str, db: Session = Depends(get_db),
                    user=Depends(require_permission("backups.download"))):
    content, name = _drive_call(db, gdrive.download, db, file_id)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
