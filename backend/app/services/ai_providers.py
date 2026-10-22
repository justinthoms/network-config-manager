# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""AI provider registry, per-user preferences and provider selection with fallback."""
from __future__ import annotations

import logging

from app.core.config import settings
from app.core.crypto import encrypt_secret, decrypt_secret
from app.models.ai_provider import AIProvider
from app.services import settings_store as store
from app.services.llm import AIError, Conn, PRESETS
from app.core.timeutil import utcnow

logger = logging.getLogger("ncm.ai")

K_DEFAULT = "ai.default"          # json {"provider_id": int, "model": str}
K_FALLBACK = "ai.fallback"        # "1" | "0"
K_ENV_IMPORTED = "ai.env_imported"
FALLBACK_CODES = {"rate", "quota", "network", "model", "auth"}


def user_key(user_id: int) -> str:
    return f"user.{user_id}.ai"


def key_hint(p: AIProvider) -> str | None:
    if not p.api_key_encrypted:
        return None
    try:
        k = decrypt_secret(p.api_key_encrypted)
    except Exception:
        return "unreadable"
    return f"…{k[-4:]}" if len(k) > 8 else "set"


def conn(p: AIProvider) -> Conn:
    key = None
    if p.api_key_encrypted:
        try:
            key = decrypt_secret(p.api_key_encrypted)
        except Exception as exc:
            raise AIError(f"The saved API key for {p.name} cannot be decrypted. Enter it again.", "auth") from exc
    return Conn(p.kind, p.base_url, key, name=p.name, provider_id=p.id)


def set_key(p: AIProvider, key: str | None):
    p.api_key_encrypted = encrypt_secret(key.strip()) if key and key.strip() else None


def visible(db, user) -> list[AIProvider]:
    q = db.query(AIProvider)
    rows = q.order_by(AIProvider.owner_id.isnot(None), AIProvider.id).all()
    return [p for p in rows if p.owner_id is None or (user is not None and p.owner_id == user.id)]


def can_edit(p: AIProvider, user) -> bool:
    from app.core.rbac import has_permission
    if p.owner_id is None:
        return has_permission(user.role, "ai.manage")
    return p.owner_id == user.id


def serialize(p: AIProvider, user=None) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "kind_label": PRESETS.get(p.kind, {}).get("label", p.kind),
        "base_url": p.base_url or PRESETS.get(p.kind, {}).get("base_url"), "default_model": p.default_model,
        "enabled": p.enabled, "shared": p.owner_id is None, "key_hint": key_hint(p),
        "last_test_ok": p.last_test_ok, "last_test_at": p.last_test_at.isoformat() if p.last_test_at else None,
        "last_error": p.last_error, "can_edit": can_edit(p, user) if user else False,
    }


def get_default(db) -> dict:
    return store.get_json(db, K_DEFAULT, {}) or {}


def get_pref(db, user) -> dict:
    return store.get_json(db, user_key(user.id), {}) or {} if user else {}


def fallback_on(db) -> bool:
    return store.get(db, K_FALLBACK, "1") == "1"


def candidates(db, user=None) -> list[tuple[AIProvider, str]]:
    """Ordered (provider, model) list: user's choice, org default, then the rest (if fallback is on)."""
    pool = [p for p in (visible(db, user) if user else
                        db.query(AIProvider).filter(AIProvider.owner_id.is_(None)).order_by(AIProvider.id).all())
            if p.enabled]
    by_id = {p.id: p for p in pool}
    order: list[tuple[AIProvider, str]] = []

    def add(pid, model):
        p = by_id.get(pid)
        if p and not any(x[0].id == p.id and x[1] == (model or p.default_model) for x in order):
            m = model or p.default_model
            if m:
                order.append((p, m))

    if user:
        pref = get_pref(db, user)
        add(pref.get("provider_id"), pref.get("model"))
    d = get_default(db)
    add(d.get("provider_id"), d.get("model"))
    if not order or fallback_on(db):
        for p in pool:
            add(p.id, None)
    return order


def is_configured(db, user=None) -> bool:
    return bool(candidates(db, user))


def run(db, user, fn):
    """Call fn(conn, model, provider) on the first provider that works. Returns (result, provider, model)."""
    cands = candidates(db, user)
    if not cands:
        raise AIError("No AI provider is set up. Add one on the AI review page (shared) or in My profile (personal).", "auth")
    errors = []
    allow_fallback = fallback_on(db)
    for i, (p, model) in enumerate(cands):
        try:
            result, used_model = fn(conn(p), model, p)
            p.last_error, p.last_test_ok, p.last_test_at = None, True, utcnow()
            db.commit()
            return result, p, used_model
        except AIError as exc:
            errors.append(f"{p.name} ({model}): {exc}")
            p.last_error = f"{utcnow().isoformat()} {exc}"
            db.commit()
            last = i == len(cands) - 1
            if last or not allow_fallback or exc.code not in FALLBACK_CODES:
                if len(errors) > 1:
                    raise AIError("All AI providers failed. " + " | ".join(errors[-3:]), exc.code) from exc
                raise
            logger.warning("AI provider %s failed (%s), trying next", p.name, exc.code)
    raise AIError("No AI provider answered.", "other")


def ensure_env_provider():
    """Import GEMINI_API_KEY from .env as a shared provider once (v0.4/v0.5 upgrade path)."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        if store.get(db, K_ENV_IMPORTED) == "1" or not settings.gemini_api_key.strip():
            return
        p = AIProvider(name="Google Gemini", kind="gemini", base_url=PRESETS["gemini"]["base_url"],
                       default_model=settings.gemini_model or "gemini-2.5-flash", enabled=True)
        set_key(p, settings.gemini_api_key)
        db.add(p)
        db.commit()
        if not get_default(db):
            store.set_json(db, K_DEFAULT, {"provider_id": p.id, "model": p.default_model}, commit=False)
        store.set(db, K_ENV_IMPORTED, "1")
        logger.info("Imported GEMINI_API_KEY from .env as shared AI provider #%s", p.id)
    except Exception:
        logger.exception("Could not import Gemini key from .env")
    finally:
        db.close()
