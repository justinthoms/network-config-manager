from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import settings
from app.core.database import get_db
from app.models.backup import Backup
from app.models.device import Device
from app.models.schedule import BackupSchedule
from app.services.diff import compare

router = APIRouter()


def _read(path: str | None):
    if not path:
        return None
    try:
        p = Path(path).resolve()
        p.relative_to(Path(settings.backup_root).resolve())
        return p.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None


def _snippet(prev_text: str, text: str, max_lines: int = 4):
    result = compare(prev_text, text, ignore_volatile=True)
    lines = []
    for r in result["rows"]:
        if r["t"] == "eq":
            continue
        if r["l"] is not None:
            lines.append("- " + r["l"].strip())
        if r["r"] is not None:
            lines.append("+ " + r["r"].strip())
        if len(lines) >= max_lines:
            break
    st = result["stats"]
    return result["changed"], lines[:max_lines], st["added"], st["removed"]


@router.get("/summary")
def summary(days: int = 14, db: Session = Depends(get_db), user=Depends(require_permission("dashboard.view"))):
    days = max(7, min(days, 90))
    now = datetime.utcnow()
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    today = now.date().isoformat()

    devices = db.query(Device).order_by(Device.hostname).all()
    hosts = {d.id: d.hostname for d in devices}
    recent = db.query(Backup).filter(Backup.created_at >= since).order_by(Backup.id.asc()).all()

    by_day = {(since + timedelta(days=i)).date().isoformat(): {"success": 0, "failed": 0, "changes": 0} for i in range(days)}

    # Previous successful hash per device (just before the window) so the first
    # backup in the window can be marked changed / unchanged correctly.
    last_sha = {}
    for d in devices:
        prev = (db.query(Backup.sha256).filter(Backup.device_id == d.id, Backup.status == "SUCCESS",
                                               Backup.created_at < since)
                .order_by(Backup.id.desc()).first())
        if prev and prev[0]:
            last_sha[d.id] = prev[0]

    change_events = []  # (backup, previous_backup_id)
    last_ok_id = {}
    for b in recent:
        key = b.created_at.date().isoformat()
        if b.status == "FAILED":
            if key in by_day:
                by_day[key]["failed"] += 1
            continue
        if b.status != "SUCCESS":
            continue
        if key in by_day:
            by_day[key]["success"] += 1
        prev_sha = last_sha.get(b.device_id)
        if b.sha256 and prev_sha and b.sha256 != prev_sha:
            if key in by_day:
                by_day[key]["changes"] += 1
            change_events.append((b, last_ok_id.get(b.device_id)))
        if b.sha256:
            last_sha[b.device_id] = b.sha256
        last_ok_id[b.device_id] = b.id

    # Latest state per device
    ports = []
    for d in devices:
        last = (db.query(Backup).filter(Backup.device_id == d.id, Backup.status.in_(["SUCCESS", "FAILED"]))
                .order_by(Backup.id.desc()).first())
        last_ok = (db.query(Backup).filter(Backup.device_id == d.id, Backup.status == "SUCCESS")
                   .order_by(Backup.id.desc()).limit(2).all())
        changed = (len(last_ok) == 2 and last_ok[0].sha256 and last_ok[1].sha256
                   and last_ok[0].sha256 != last_ok[1].sha256)
        if not d.enabled:
            state = "disabled"
        elif not last:
            state = "never"
        elif last.status == "FAILED":
            state = "failed"
        elif last.created_at and last.created_at < now - timedelta(days=2):
            state = "stale"
        else:
            state = "ok"
        ports.append({
            "id": d.id, "hostname": d.hostname, "vendor": d.vendor, "model": d.model, "site": d.site,
            "enabled": d.enabled, "state": state, "changed": bool(changed),
            "last_backup_at": last.created_at.isoformat() if last and last.created_at else None,
            "last_error": last.error if last and last.status == "FAILED" else None,
        })

    # Most recent config changes with a short diff snippet
    recent_changes = []
    for b, prev_id in reversed(change_events):
        if len(recent_changes) >= 5:
            break
        prev = db.get(Backup, prev_id) if prev_id else (
            db.query(Backup).filter(Backup.device_id == b.device_id, Backup.status == "SUCCESS", Backup.id < b.id)
            .order_by(Backup.id.desc()).first())
        if not prev:
            continue
        a_text, b_text = _read(prev.file_path), _read(b.file_path)
        if a_text is None or b_text is None:
            continue
        changed, lines, added, removed = _snippet(a_text, b_text)
        if not changed:
            continue  # only volatile lines differed
        recent_changes.append({
            "backup_id": b.id, "previous_id": prev.id, "device_id": b.device_id,
            "hostname": hosts.get(b.device_id, f"Device {b.device_id}"),
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "added": added, "removed": removed, "lines": lines,
        })
    if recent_changes:
        from app.models.ai_review import AIReview
        ids = [c["backup_id"] for c in recent_changes]
        for r in (db.query(AIReview).filter(AIReview.kind == "review", AIReview.backup_b.in_(ids))
                  .order_by(AIReview.id.asc()).all()):
            for c in recent_changes:
                if c["backup_id"] == r.backup_b and c["previous_id"] == r.backup_a:
                    c["risk"] = r.risk
                    c["review_id"] = r.id

    window_ok = sum(v["success"] for v in by_day.values())
    window_failed = sum(v["failed"] for v in by_day.values())
    return {
        "days": days,
        "devices": len(devices),
        "devices_enabled": sum(1 for d in devices if d.enabled),
        "backups_success": db.query(Backup).filter(Backup.status == "SUCCESS").count(),
        "backups_failed": db.query(Backup).filter(Backup.status == "FAILED").count(),
        "schedules_active": db.query(BackupSchedule).filter(BackupSchedule.enabled.is_(True)).count(),
        "success_rate": round(window_ok * 100 / (window_ok + window_failed), 1) if (window_ok + window_failed) else None,
        "window_success": window_ok,
        "window_failed": window_failed,
        "window_changes": sum(v["changes"] for v in by_day.values()),
        "changes_today": by_day.get(today, {}).get("changes", 0),
        "failed_open": sum(1 for p in ports if p["state"] == "failed"),
        "in_drive": db.query(Backup).filter(Backup.gdrive_file_id.isnot(None)).count(),
        "activity": [{"date": k, **v} for k, v in by_day.items()],
        "ports": ports,
        "recent_changes": recent_changes,
        "sites": sorted({d.site or "DEFAULT" for d in devices}),
    }
