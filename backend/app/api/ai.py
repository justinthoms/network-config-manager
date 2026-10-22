# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.database import get_db
from app.models.ai_review import AIReview
from app.models.ai_provider import AIProvider
from app.services import ai_providers, llm
from app.models.backup import Backup
from app.models.device import Device
from app.services import ai_review as ai
from app.services import settings_store as store
from app.services.audit import record_audit
from app.core.timeutil import utcnow

router = APIRouter()


class SettingsIn(BaseModel):
    mask_ips: bool | None = None
    auto_review: bool | None = None
    live_commands: bool | None = None
    fallback: bool | None = None
    default_provider_id: int | None = None
    default_model: str | None = Field(default=None, max_length=200)


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=8000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    tz_offset_minutes: int = Field(default=0, ge=-720, le=840)


def _out(row: AIReview, hostname: str | None = None):
    return {
        "id": row.id, "kind": row.kind, "device_id": row.device_id, "hostname": hostname,
        "backup_a": row.backup_a, "backup_b": row.backup_b, "model": row.model,
        "risk": row.risk, "score": row.score, "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "result": json.loads(row.result),
    }


def _ok_backup(db, backup_id) -> Backup:
    b = db.get(Backup, backup_id)
    if not b or b.status != "SUCCESS":
        raise HTTPException(404, f"Successful backup #{backup_id} not found")
    return b


def _run(db, fn, *args):
    try:
        return fn(*args)
    except ai.AIError as exc:
        ai.record_error(db, str(exc))
        raise HTTPException(400, str(exc))
    except (OSError, ValueError):
        raise HTTPException(404, "Backup file is missing on disk")


@router.get("/status")
def ai_status(db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    data = ai.status(db, user)
    data["reviews"] = db.query(AIReview).count()
    return data


@router.put("/settings")
def ai_settings(data: SettingsIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.manage"))):
    if data.mask_ips is not None:
        store.set(db, ai.K_MASK_IPS, "1" if data.mask_ips else "0", commit=False)
    if data.auto_review is not None:
        store.set(db, ai.K_AUTO, "1" if data.auto_review else "0", commit=False)
    if data.live_commands is not None:
        if user.role not in ("super_admin", "admin"):
            raise HTTPException(403, "Only admins can change live command access")
        store.set(db, "ai.live_commands", "1" if data.live_commands else "0", commit=False)
    if data.fallback is not None:
        store.set(db, ai_providers.K_FALLBACK, "1" if data.fallback else "0", commit=False)
    if data.default_provider_id is not None:
        p = db.get(AIProvider, data.default_provider_id)
        if not p or p.owner_id is not None:
            raise HTTPException(400, "Default provider must be a shared provider")
        store.set_json(db, ai_providers.K_DEFAULT, {"provider_id": p.id, "model": data.default_model or p.default_model},
                       commit=False)
    db.commit()
    record_audit(db, user, "AI_SETTINGS", "ai", "settings",
                 details=f"mask_ips={data.mask_ips};auto_review={data.auto_review};live={data.live_commands}")
    return ai.status(db, user)


@router.post("/validate/{backup_id}")
def validate(backup_id: int, refresh: bool = False, db: Session = Depends(get_db),
             user=Depends(require_permission("ai.run"))):
    b = _ok_backup(db, backup_id)
    if not refresh:
        cached = (db.query(AIReview).filter(AIReview.kind == "validate", AIReview.backup_b == b.id)
                  .order_by(AIReview.id.desc()).first())
        if cached:
            return {**_out(cached), "cached": True}
    device = db.get(Device, b.device_id)
    result = _run(db, ai.validate_config, db, b, device, user)
    row = ai.save(db, "validate", b.device_id, None, b.id, result, user=user.username)
    record_audit(db, user, "AI_VALIDATE", "backup", b.id, details=f"score={result.get('score')}")
    return {**_out(row, device.hostname), "cached": False}


@router.post("/review")
def review(backup_a: int, backup_b: int, refresh: bool = False, db: Session = Depends(get_db),
           user=Depends(require_permission("ai.run"))):
    a, b = _ok_backup(db, backup_a), _ok_backup(db, backup_b)
    if a.device_id != b.device_id:
        raise HTTPException(400, "AI change review works on two backups of the same device")
    if a.id > b.id:
        a, b = b, a
    if not refresh:
        cached = (db.query(AIReview).filter(AIReview.kind == "review", AIReview.backup_a == a.id,
                                            AIReview.backup_b == b.id)
                  .order_by(AIReview.id.desc()).first())
        if cached:
            return {**_out(cached), "cached": True}
    device = db.get(Device, b.device_id)
    result = _run(db, ai.review_change, db, a, b, device, user)
    row = ai.save(db, "review", b.device_id, a.id, b.id, result, user=user.username)
    record_audit(db, user, "AI_REVIEW", "backup", f"{a.id}..{b.id}", details=f"risk={result.get('risk')}")
    return {**_out(row, device.hostname), "cached": False}


@router.get("/reviews")
def list_reviews(device_id: int | None = None, kind: str | None = None, limit: int = 100,
                 db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    q = db.query(AIReview)
    if device_id:
        q = q.filter(AIReview.device_id == device_id)
    if kind in ("validate", "review"):
        q = q.filter(AIReview.kind == kind)
    rows = q.order_by(AIReview.id.desc()).limit(max(1, min(limit, 500))).all()
    hosts = {d.id: d.hostname for d in db.query(Device).all()}
    out = []
    for r in rows:
        item = _out(r, hosts.get(r.device_id))
        res = item.pop("result")
        item["summary"] = res.get("summary", "")
        item["findings"] = len(res.get("findings", []))
        item["top_severity"] = res["findings"][0]["severity"] if res.get("findings") else None
        out.append(item)
    return out


@router.get("/reviews/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    row = db.get(AIReview, review_id)
    if not row:
        raise HTTPException(404, "Review not found")
    d = db.get(Device, row.device_id) if row.device_id else None
    return _out(row, d.hostname if d else None)


@router.get("/lookup")
def lookup(backup_ids: str, db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    """Latest review risk / validation score for a set of backup ids (for badges)."""
    ids = [int(x) for x in backup_ids.split(",") if x.strip().isdigit()][:300]
    if not ids:
        return {}
    rows = db.query(AIReview).filter(AIReview.backup_b.in_(ids)).order_by(AIReview.id.asc()).all()
    out = {}
    for r in rows:
        e = out.setdefault(str(r.backup_b), {})
        if r.kind == "review":
            e["risk"] = r.risk; e["review_id"] = r.id; e["previous_id"] = r.backup_a
        else:
            e["score"] = r.score; e["validate_id"] = r.id
    return out


@router.post("/chat")
def chat(data: ChatIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    from app.core.rbac import has_permission
    from app.services import assistant
    try:
        return assistant.chat(db, user, data.message.strip(), [t.model_dump() for t in data.history],
                              tz_offset_min=data.tz_offset_minutes,
                              can_live=has_permission(user.role, "devices.test"))
    except ai.AIError as exc:
        ai.record_error(db, str(exc))
        raise HTTPException(400, str(exc))


# ───────────────────────── providers ─────────────────────────

class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, max_length=200)
    enabled: bool = True
    shared: bool = False


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    default_model: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class ProbeIn(BaseModel):
    kind: str
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    provider_id: int | None = None      # reuse the saved key of an existing provider


class PrefIn(BaseModel):
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=200)


def _check_kind(kind):
    if kind not in llm.PRESETS:
        raise HTTPException(400, f"Unknown provider type. Use one of: {', '.join(llm.PRESETS)}")


def _clean_url(url):
    url = (url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Base URL must start with http:// or https://")
    return url or None


def _provider_or_404(db, pid, user, edit=False) -> AIProvider:
    p = db.get(AIProvider, pid)
    if not p or (p.owner_id is not None and p.owner_id != user.id):
        raise HTTPException(404, "Provider not found")
    if edit and not ai_providers.can_edit(p, user):
        raise HTTPException(403, "You cannot change this provider")
    return p


@router.get("/presets")
def presets(user=Depends(require_permission("ai.view"))):
    return llm.PRESETS


@router.get("/providers")
def list_providers(db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    return [ai_providers.serialize(p, user) for p in ai_providers.visible(db, user)]


@router.post("/providers")
def create_provider(data: ProviderIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    from app.core.rbac import has_permission
    _check_kind(data.kind)
    if data.shared and not has_permission(user.role, "ai.manage"):
        raise HTTPException(403, "Only admins can add shared providers")
    if data.kind == "custom" and not data.base_url:
        raise HTTPException(400, "Base URL is required for a custom provider")
    if llm.PRESETS[data.kind]["needs_key"] and not (data.api_key or "").strip():
        raise HTTPException(400, "API key is required for this provider")
    p = AIProvider(name=data.name.strip(), kind=data.kind,
                   base_url=_clean_url(data.base_url) or llm.PRESETS[data.kind]["base_url"],
                   default_model=(data.default_model or "").strip() or None, enabled=data.enabled,
                   owner_id=None if data.shared else user.id)
    ai_providers.set_key(p, data.api_key)
    db.add(p)
    db.commit()
    db.refresh(p)
    if p.owner_id is None and not ai_providers.get_default(db) and p.default_model:
        store.set_json(db, ai_providers.K_DEFAULT, {"provider_id": p.id, "model": p.default_model})
    record_audit(db, user, "AI_PROVIDER_ADD", "ai_provider", p.id, details=f"{p.kind} {'shared' if p.owner_id is None else 'personal'}")
    return ai_providers.serialize(p, user)


@router.patch("/providers/{pid}")
def update_provider(pid: int, data: ProviderPatch, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    p = _provider_or_404(db, pid, user, edit=True)
    if data.name is not None:
        p.name = data.name.strip()
    if data.base_url is not None:
        p.base_url = _clean_url(data.base_url) or llm.PRESETS[p.kind]["base_url"]
    if data.api_key:
        ai_providers.set_key(p, data.api_key)
    if data.default_model is not None:
        p.default_model = data.default_model.strip() or None
    if data.enabled is not None:
        p.enabled = data.enabled
    db.commit()
    record_audit(db, user, "AI_PROVIDER_EDIT", "ai_provider", p.id, details="key changed" if data.api_key else None)
    return ai_providers.serialize(p, user)


@router.delete("/providers/{pid}")
def delete_provider(pid: int, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    p = _provider_or_404(db, pid, user, edit=True)
    if ai_providers.get_default(db).get("provider_id") == p.id:
        store.set(db, ai_providers.K_DEFAULT, None, commit=False)
    db.delete(p)
    db.commit()
    record_audit(db, user, "AI_PROVIDER_DELETE", "ai_provider", pid)
    return {"success": True}


def _probe_conn(db, data: ProbeIn, user) -> llm.Conn:
    _check_kind(data.kind)
    key = (data.api_key or "").strip()
    if not key and data.provider_id:
        saved = _provider_or_404(db, data.provider_id, user)
        # A saved key only ever goes to the provider's own saved endpoint, never a URL from the request.
        return ai_providers.conn(saved)
    return llm.Conn(data.kind, _clean_url(data.base_url) or llm.PRESETS[data.kind]["base_url"], key,
                    name=llm.PRESETS[data.kind]["label"])


@router.post("/probe/models")
def probe_models(data: ProbeIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    """List models for a provider before or after saving it."""
    try:
        return llm.list_models(_probe_conn(db, data, user))
    except llm.AIError as exc:
        raise HTTPException(400, str(exc))


@router.post("/probe/test")
def probe_test(data: ProbeIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    if not data.model:
        raise HTTPException(400, "Choose a model to test")
    try:
        res = llm.test(_probe_conn(db, data, user), data.model)
    except llm.AIError as exc:
        res = {"ok": False, "error": str(exc)}
    if data.provider_id:
        p = db.get(AIProvider, data.provider_id)
        if p and (ai_providers.can_edit(p, user) or p.owner_id is None):
            p.last_test_ok, p.last_test_at = res["ok"], utcnow()
            p.last_error = None if res["ok"] else res.get("error")
            db.commit()
    return res


@router.get("/providers/{pid}/models")
def provider_models(pid: int, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    p = _provider_or_404(db, pid, user)
    try:
        return llm.list_models(ai_providers.conn(p))
    except llm.AIError as exc:
        raise HTTPException(400, str(exc))


@router.get("/preferences")
def get_preferences(db: Session = Depends(get_db), user=Depends(require_permission("ai.view"))):
    pref = ai_providers.get_pref(db, user)
    cands = ai_providers.candidates(db, user)
    return {"provider_id": pref.get("provider_id"), "model": pref.get("model"),
            "effective_provider": cands[0][0].name if cands else None, "effective_model": cands[0][1] if cands else None}


@router.put("/preferences")
def set_preferences(data: PrefIn, db: Session = Depends(get_db), user=Depends(require_permission("ai.run"))):
    if data.provider_id is None:
        store.set(db, ai_providers.user_key(user.id), None)
    else:
        p = _provider_or_404(db, data.provider_id, user)
        if not p.enabled:
            raise HTTPException(400, "That provider is disabled")
        store.set_json(db, ai_providers.user_key(user.id), {"provider_id": p.id, "model": (data.model or "").strip() or p.default_model})
    return get_preferences(db, user)
