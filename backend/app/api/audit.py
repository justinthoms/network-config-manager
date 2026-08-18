from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require_permission
from app.core.database import get_db
from app.models.audit import AuditLog
from app.models.user import User

router = APIRouter()

@router.get("/")
def list_audit(limit: int = 200, user_id: int | None = None, action: str | None = None,
               db: Session = Depends(get_db),
               user: User = Depends(require_permission("audit.view"))):
    limit = max(1, min(limit, 1000))
    q = db.query(AuditLog)
    if user_id is not None: q = q.filter(AuditLog.user_id == user_id)
    if action: q = q.filter(AuditLog.action == action)
    rows = q.order_by(AuditLog.id.desc()).limit(limit).all()
    return [{
        "id": x.id, "user_id": x.user_id, "username": x.username,
        "action": x.action, "resource": x.resource, "resource_id": x.resource_id,
        "status": x.status, "details": x.details,
        "source_ip": x.source_ip, "created_at": x.created_at.isoformat() if x.created_at else None
    } for x in rows]
