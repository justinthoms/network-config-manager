from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.backup import Backup
from app.models.device import Device
from app.services import onedrive
from app.services import settings_store as store
from app.services.audit import record_audit

router = APIRouter()


class ConfigIn(BaseModel):
    client_id: str = Field(min_length=10, max_length=100)
    tenant_id: str | None = Field(default=None, max_length=100)


class SettingsIn(BaseModel):
    auto_sync: bool | None = None
    root_folder: str | None = Field(default=None, min_length=1, max_length=100)
    layout: str | None = Field(default=None, pattern="^(hostname|date)$")


def _call(db, fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except onedrive.OneDriveError as exc:
        onedrive.record_error(db, str(exc))
        raise HTTPException(400, str(exc))


def _pending_q(db):
    return db.query(Backup).filter(Backup.status == "SUCCESS", Backup.onedrive_item_id.is_(None))


@router.get("/status")
def od_status(db: Session = Depends(get_db), user=Depends(require_permission("cloud.view"))):
    data = onedrive.status(db)
    from app.core.crypto import check_key
    key_err = check_key()
    if key_err:
        data["last_error"] = key_err
    data["synced_backups"] = db.query(Backup).filter(Backup.onedrive_item_id.isnot(None)).count()
    data["pending_backups"] = _pending_q(db).count()
    return data


@router.put("/config")
def od_config(data: ConfigIn, db: Session = Depends(get_db),
              user=Depends(require_permission("cloud.manage"))):
    if user.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can change the OneDrive app registration")
    if onedrive.credentials_source(db) == "env":
        raise HTTPException(400, "The client ID is set in the .env file. Edit ONEDRIVE_CLIENT_ID / ONEDRIVE_TENANT_ID there.")
    onedrive.save_client_config(db, data.client_id, data.tenant_id)
    record_audit(db, user, "ONEDRIVE_CONFIG", "cloud", "onedrive")
    return onedrive.status(db)


@router.post("/device/start")
def od_device_start(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    return _call(db, onedrive.start_device_flow, db, user.id)


@router.post("/device/poll")
def od_device_poll(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    try:
        result = onedrive.poll_device_flow(db, user.id)
    except Exception as exc:  # never 500 here: the browser is polling and needs a message
        db.rollback()
        return {"state": "error", "message": str(exc)}
    if result["state"] == "connected":
        st = result["status"]
        record_audit(db, user, "ONEDRIVE_CONNECT", "cloud", "onedrive",
                     details=st.get("account_email") or st.get("account_name"))
    elif result["state"] == "error":
        try:
            onedrive.record_error(db, result["message"])
        except Exception:
            db.rollback()
    return result


@router.post("/device/cancel")
def od_device_cancel(user=Depends(require_permission("cloud.manage"))):
    onedrive.cancel_device_flow(user.id)
    return {"ok": True}


@router.post("/disconnect")
def od_disconnect(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    onedrive.disconnect(db)
    record_audit(db, user, "ONEDRIVE_DISCONNECT", "cloud", "onedrive")
    return onedrive.status(db)


@router.put("/settings")
def od_settings(data: SettingsIn, db: Session = Depends(get_db),
                user=Depends(require_permission("cloud.manage"))):
    if data.auto_sync is not None:
        store.set(db, onedrive.K_AUTO, "1" if data.auto_sync else "0", commit=False)
    if data.root_folder is not None:
        name = onedrive._safe(data.root_folder.replace("/", "-"))
        if name != onedrive.root_folder_name(db):
            store.set(db, onedrive.K_ROOT_NAME, name, commit=False)
            store.set(db, onedrive.K_ROOT_URL, None, commit=False)
    if data.layout is not None:
        store.set(db, onedrive.K_LAYOUT, data.layout, commit=False)
    db.commit()
    record_audit(db, user, "ONEDRIVE_SETTINGS", "cloud", "onedrive",
                 details=f"auto_sync={data.auto_sync};root={data.root_folder};layout={data.layout}")
    return onedrive.status(db)


@router.post("/test")
def od_test(db: Session = Depends(get_db), user=Depends(require_permission("cloud.manage"))):
    result = _call(db, onedrive.test_upload, db)
    record_audit(db, user, "ONEDRIVE_TEST", "cloud", "onedrive", details=result.get("name"))
    return result


@router.post("/upload/{backup_id}")
def od_upload(backup_id: int, db: Session = Depends(get_db),
              user=Depends(require_permission("cloud.sync"))):
    backup = db.get(Backup, backup_id)
    if not backup or backup.status != "SUCCESS":
        raise HTTPException(404, "Successful backup not found")
    device = db.get(Device, backup.device_id)
    result = _call(db, onedrive.upload_backup, db, backup,
                   device.hostname if device else f"device-{backup.device_id}")
    record_audit(db, user, "ONEDRIVE_UPLOAD", "backup", backup_id, details=result.get("name"))
    return {"success": True, "file": result}


@router.post("/sync")
def od_sync(limit: int = 50, db: Session = Depends(get_db),
            user=Depends(require_permission("cloud.sync"))):
    """Upload successful backups that are not in OneDrive yet (oldest first)."""
    if not onedrive.is_connected(db):
        raise HTTPException(400, "OneDrive is not connected")
    limit = max(1, min(limit, 500))
    pending = _pending_q(db).order_by(Backup.id.asc()).limit(limit).all()
    hosts = {d.id: d.hostname for d in db.query(Device).all()}
    uploaded, failed, errors = 0, 0, []
    for b in pending:
        try:
            onedrive.upload_backup(db, b, hosts.get(b.device_id, f"device-{b.device_id}"))
            uploaded += 1
        except Exception as exc:
            failed += 1
            errors.append(f"#{b.id}: {exc}")
            if isinstance(exc, onedrive.OneDriveError) and ("connected" in str(exc).lower()
                                                            or "reconnect" in str(exc).lower()):
                break
    if errors:
        onedrive.record_error(db, errors[-1])
    record_audit(db, user, "ONEDRIVE_SYNC", "cloud", "onedrive",
                 status="SUCCESS" if not failed else "FAILED",
                 details=f"uploaded={uploaded};failed={failed}")
    return {"uploaded": uploaded, "failed": failed, "remaining": _pending_q(db).count(),
            "errors": errors[:10]}


@router.get("/files")
def od_files(limit: int = 100, db: Session = Depends(get_db),
             user=Depends(require_permission("cloud.view"))):
    """Recent backups NCM has copied to OneDrive (from NCM's own records, so new uploads show at once)."""
    limit = max(1, min(limit, 500))
    rows = (db.query(Backup, Device.hostname)
            .outerjoin(Device, Device.id == Backup.device_id)
            .filter(Backup.onedrive_item_id.isnot(None))
            .order_by(Backup.onedrive_synced_at.desc()).limit(limit).all())
    out = []
    for b, host in rows:
        host = host or f"device-{b.device_id}"
        when = b.created_at
        out.append({
            "id": b.onedrive_item_id, "backup_id": b.id, "hostname": host,
            "name": f"{host}_{when:%Y%m%d_%H%M%S}.conf" if when else f"backup-{b.id}.conf",
            "size": b.size_bytes or 0,
            "uploaded_at": b.onedrive_synced_at.isoformat() if b.onedrive_synced_at else None,
        })
    return out


def _own_item(db, item_id: str):
    """The token can reach the whole drive; only serve items NCM uploaded as backups."""
    if not db.query(Backup.id).filter(Backup.onedrive_item_id == item_id).first():
        raise HTTPException(404, "Not an NCM backup in OneDrive")


@router.get("/files/{item_id}/download")
def od_download(item_id: str, db: Session = Depends(get_db),
                user=Depends(require_permission("backups.download"))):
    _own_item(db, item_id)
    content, name = _call(db, onedrive.download, db, item_id)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/files/{item_id}/link")
def od_link(item_id: str, db: Session = Depends(get_db),
            user=Depends(require_permission("cloud.view"))):
    _own_item(db, item_id)
    info = _call(db, onedrive.item_info, db, item_id)
    return {"url": info.get("webUrl")}
