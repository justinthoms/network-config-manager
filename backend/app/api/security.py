# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.user import User
from app.services import hardening

router = APIRouter()


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
