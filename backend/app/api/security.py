# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_permission
from app.core.database import get_db
from app.models.user import User
from app.services import hardening, telemetry
from app.services.audit import record_audit

router = APIRouter()


@router.get("/telemetry")
def telemetry_status(db: Session = Depends(get_db), user=Depends(require_permission("security.view"))):
    out = telemetry.what_is_sent(db)
    out["can_change"] = user.role == "super_admin"
    return out


class TelemetryIn(BaseModel):
    enabled: bool


@router.put("/telemetry")
def set_telemetry(data: TelemetryIn, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    if user.role != "super_admin":
        raise HTTPException(403, "Only a Super Admin can change usage reporting")
    telemetry.set_enabled(db, data.enabled)
    record_audit(db, user, "TELEMETRY_SETTING", "system", "telemetry",
                 details=f"enabled={data.enabled}", source_ip=request.client.host if request.client else None)
    return telemetry.what_is_sent(db)


@router.get("/status")
def security_status(request: Request, db: Session = Depends(get_db),
                    user=Depends(require_permission("security.view"))):
    items = hardening.checks(db, request)
    rank = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    items.sort(key=lambda x: rank.get(x["status"], 9))
    users = db.query(User).order_by(User.username).all()
    return {
        "checks": items,
        "counts": {k: sum(1 for i in items if i["status"] == k) for k in rank},
        "users": [{"username": u.username, "role": u.role, "is_active": u.is_active,
                   "must_change_password": bool(u.must_change_password),
                   "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
                   "last_login_ip": u.last_login_ip,
                   "password_changed_at": u.password_changed_at.isoformat() if u.password_changed_at else None}
                  for u in users],
    }
