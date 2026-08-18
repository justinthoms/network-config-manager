from app.models.audit import AuditLog

def record_audit(db, user, action, resource=None, resource_id=None,
                 status="SUCCESS", details=None, source_ip=None):
    row = AuditLog(
        user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        action=action,
        resource=resource,
        resource_id=str(resource_id) if resource_id is not None else None,
        status=status,
        details=details,
        source_ip=source_ip,
    )
    db.add(row)
    db.commit()
    return row
