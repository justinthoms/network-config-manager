# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.device import Device
from app.models.health import HealthCheck
from app.services import health
from app.services import settings_store as store
from app.services.audit import record_audit
from app.services.network import validate_show_command, LiveCommandError

router = APIRouter()


class SettingsIn(BaseModel):
    schedule_enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    retention: int | None = Field(default=None, ge=1, le=1000)
    ai_mode: str | None = Field(default=None, pattern="^(off|issues|all)$")
    ai_language: str | None = Field(default=None, pattern="^(English|Malayalam)$")
    log_window_hours: int | None = Field(default=None, ge=1, le=720)
    commands: dict[str, list[str] | None] | None = None   # vendor -> list, or null to restore defaults


def _latest_map(db) -> dict[int, HealthCheck]:
    sub = (db.query(HealthCheck.device_id, func.max(HealthCheck.id).label("mid"))
           .group_by(HealthCheck.device_id).subquery())
    rows = db.query(HealthCheck).join(sub, HealthCheck.id == sub.c.mid).all()
    return {r.device_id: r for r in rows}


@router.get("/status")
def health_status(db: Session = Depends(get_db), user=Depends(require_permission("health.view"))):
    latest = _latest_map(db)
    devices = db.query(Device).order_by(Device.hostname).all()
    out, counts = [], {"ok": 0, "warning": 0, "critical": 0, "failed": 0, "never": 0}
    for d in devices:
        row = latest.get(d.id)
        item = {"device_id": d.id, "hostname": d.hostname, "vendor": d.vendor, "model": d.model,
                "site": d.site, "ip": d.management_ip, "enabled": d.enabled,
                "check": health._serialize(row, d.hostname) if row else None}
        counts[row.status if row else "never"] += 1
        out.append(item)
    from app.services.ai_review import is_configured
    return {"devices": out, "counts": counts, "settings": health.settings_view(db),
            "run": health.run_state(), "last_run": store.get_json(db, health.K_LAST_RUN),
            "next_run_at": health.next_run_at(), "ai_configured": is_configured(db, user)}


@router.post("/run/{device_id}")
def run_one(device_id: int, db: Session = Depends(get_db), user=Depends(require_permission("health.run"))):
    device = db.get(Device, device_id)
    if not device:
        raise HTTPException(404, "Device not found")
    row = health.run_check(db, device, user.username, "manual")
    record_audit(db, user, "HEALTH_CHECK", "device", device_id,
                 status="FAILED" if row.status == "failed" else "SUCCESS",
                 details=f"status={row.status}" + (f";error={row.error[:150]}" if row.error else ""))
    out = health._serialize(row, device.hostname, full=True)
    from app.services.ai_review import is_configured
    out["ai_suggested"] = health.should_ai(db, row.status) and is_configured(db, user)
    return out


@router.post("/run-all")
def run_all(db: Session = Depends(get_db), user=Depends(require_permission("health.run"))):
    if not health.run_all("manual", user.username):
        raise HTTPException(409, "A health check of all devices is already running")
    record_audit(db, user, "HEALTH_CHECK_ALL", "health", "all")
    return health.run_state()


@router.get("/run-state")
def run_state(user=Depends(require_permission("health.view"))):
    return health.run_state()


@router.get("/checks")
def list_checks(device_id: int | None = None, limit: int = 50, db: Session = Depends(get_db),
                user=Depends(require_permission("health.view"))):
    q = db.query(HealthCheck)
    if device_id:
        q = q.filter(HealthCheck.device_id == device_id)
    rows = q.order_by(HealthCheck.id.desc()).limit(max(1, min(limit, 500))).all()
    hosts = {d.id: d.hostname for d in db.query(Device).all()}
    return [health._serialize(r, hosts.get(r.device_id)) for r in rows]


@router.get("/checks/{check_id}")
def get_check(check_id: int, db: Session = Depends(get_db), user=Depends(require_permission("health.view"))):
    row = db.get(HealthCheck, check_id)
    if not row:
        raise HTTPException(404, "Health check not found")
    device = db.get(Device, row.device_id)
    out = health._serialize(row, device.hostname if device else None, full=True)
    from app.services.ai_review import is_configured
    out["ai_configured"] = is_configured(db, user)
    return out


@router.post("/checks/{check_id}/ai")
def check_ai(check_id: int, refresh: bool = False, db: Session = Depends(get_db),
             user=Depends(require_permission("ai.run"))):
    from app.services.llm import AIError
    row = db.get(HealthCheck, check_id)
    if not row:
        raise HTTPException(404, "Health check not found")
    if row.status == "failed":
        raise HTTPException(400, "This check failed before any output was collected")
    if row.ai and not refresh:
        import json
        return json.loads(row.ai)
    device = db.get(Device, row.device_id)
    try:
        result = health.ai_summary(db, row, device, user)
    except AIError as exc:
        raise HTTPException(502 if getattr(exc, "code", "") != "auth" else 400, str(exc))
    record_audit(db, user, "HEALTH_AI", "health", check_id, details=f"{result.get('_provider')}: {result.get('_model')}")
    return result


@router.put("/settings")
def put_settings(data: SettingsIn, db: Session = Depends(get_db), user=Depends(require_permission("health.manage"))):
    if data.commands and user.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can change health check commands")
    if data.commands:
        for vendor, cmds in data.commands.items():
            if vendor not in health.DEFAULT_COMMANDS:
                raise HTTPException(400, f"Unknown vendor {vendor}")
            if cmds is None:
                store.set(db, health.K_CMDS + vendor, None, commit=False)
                continue
            clean = []
            for c in cmds:
                c = " ".join(c.split())
                if not c:
                    continue
                try:
                    clean.append(validate_show_command(vendor, c))
                except LiveCommandError as exc:
                    raise HTTPException(400, f"{vendor}: '{c}': {exc}")
            if not clean or len(clean) > 25:
                raise HTTPException(400, f"{vendor}: give between 1 and 25 commands")
            same = clean == health.DEFAULT_COMMANDS[vendor]
            store.set_json(db, health.K_CMDS + vendor, clean, commit=False) if not same else \
                store.set(db, health.K_CMDS + vendor, None, commit=False)
    pairs = [(health.K_SCHED, None if data.schedule_enabled is None else ("1" if data.schedule_enabled else "0")),
             (health.K_INTERVAL, data.interval_minutes), (health.K_RETENTION, data.retention),
             (health.K_AI_MODE, data.ai_mode), (health.K_AI_LANG, data.ai_language),
             (health.K_WINDOW, data.log_window_hours)]
    for key, value in pairs:
        if value is not None:
            store.set(db, key, value, commit=False)
    db.commit()
    if data.schedule_enabled is not None or data.interval_minutes is not None:
        health.schedule_job(db)
    record_audit(db, user, "HEALTH_SETTINGS", "health", "settings",
                 details=data.model_dump_json(exclude_none=True)[:500])
    return {"settings": health.settings_view(db), "next_run_at": health.next_run_at()}
