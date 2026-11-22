# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Version information, update checks and in-app upgrades (see app/services/updater.py)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_permission
from app.core.database import get_db
from app.core.rbac import has_permission
from app.core.security import verify_password
from app.models.user import User
from app.services import settings_store as store
from app.services import updater
from app.services.audit import record_audit
from app.version import __version__

router = APIRouter()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _confirm_password(db: Session, user: User, password: str, request: Request, action: str):
    u = db.get(User, user.id)
    if not verify_password(password or "", u.password_hash if u else None):
        record_audit(db, user, action, "system", __version__, "FAILED", "wrong password", _ip(request))
        raise HTTPException(400, "Your password is not correct")


@router.get("/version")
def version(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """The running version for everyone; whether an update exists for roles that may see it."""
    out = {"version": __version__}
    if has_permission(user.role, "system.view"):
        last = store.get_json(db, updater.K_LATEST) or {}
        latest = (last.get("latest") or {}).get("version")
        out.update(latest=latest, update_available=bool(latest and updater.is_newer(latest)),
                   checked_at=last.get("checked_at"), upgrade=(updater.read_state() or {}).get("status"))
    return out


@router.get("/update")
def update_status(db: Session = Depends(get_db), user: User = Depends(require_permission("system.view"))):
    out = updater.status(db)
    out["snapshots"] = updater.snapshots()
    out["can_upgrade"] = has_permission(user.role, "system.update")
    return out


@router.get("/update/state")
def upgrade_state(user: User = Depends(require_permission("system.view"))):
    return {"version": __version__, "upgrade": updater.read_state()}


@router.post("/update/check")
def check_now(db: Session = Depends(get_db), user: User = Depends(require_permission("system.view"))):
    res = updater.check(db)
    if res.get("error"):
        raise HTTPException(502, res["error"])
    return update_status(db, user)


class UpdateSettingsIn(BaseModel):
    auto_check: bool


@router.put("/update/settings")
def update_settings(data: UpdateSettingsIn, db: Session = Depends(get_db),
                    user: User = Depends(require_permission("system.update"))):
    store.set(db, updater.K_AUTO, "1" if data.auto_check else "0")
    record_audit(db, user, "UPDATE_SETTINGS", "system", "auto_check", details=str(data.auto_check))
    return {"auto_check": data.auto_check}


class UpgradeIn(BaseModel):
    version: str = Field(max_length=50)
    password: str = Field(max_length=200)


@router.post("/update/start")
def start_upgrade(data: UpgradeIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_permission("system.update"))):
    _confirm_password(db, user, data.password, request, "SYSTEM_UPGRADE")
    try:
        # the download address always comes from GitHub, never from the browser
        release = next((r for r in updater.fetch_releases() if r["version"] == data.version.lstrip("v")), None)
        if not release:
            raise updater.UpdateError(f"Release {data.version} was not found on GitHub")
        state = updater.start_upgrade(release, user.username)
    except updater.UpdateError as exc:
        raise HTTPException(400, str(exc))
    record_audit(db, user, "SYSTEM_UPGRADE", "system", __version__, "STARTED",
                 f"{__version__} -> {release['version']}", _ip(request))
    return state


class RollbackIn(BaseModel):
    snapshot: str = Field(max_length=100)
    restore_database: bool = False
    password: str = Field(max_length=200)


@router.post("/update/rollback")
def rollback(data: RollbackIn, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_permission("system.update"))):
    _confirm_password(db, user, data.password, request, "SYSTEM_ROLLBACK")
    try:
        state = updater.start_rollback(data.snapshot, data.restore_database, user.username)
    except updater.UpdateError as exc:
        raise HTTPException(400, str(exc))
    record_audit(db, user, "SYSTEM_ROLLBACK", "system", __version__, "STARTED",
                 f"to {data.snapshot}" + (" with database" if data.restore_database else ""), _ip(request))
    return state
