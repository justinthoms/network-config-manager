"""Startup hardening and the security self-check shown to admins."""
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from app.core import config as cfg
from app.core.config import settings

logger = logging.getLogger("ncm.security")


def _db_file() -> Path | None:
    url = settings.database_url
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1)).resolve()
    return None


def private_paths() -> list[tuple[Path, int]]:
    out = [(cfg.ENV_FILE, 0o600), (cfg.SECRET_FILE, 0o600), (Path(settings.backup_root).resolve(), 0o700)]
    db = _db_file()
    if db:
        out.append((db, 0o600))
    return out


def restrict_permissions():
    """New files are private to the NCM user; tighten existing sensitive files and the backup folder."""
    if os.name != "posix" or not settings.restrict_file_permissions:
        return
    os.umask(0o077)
    for path, mode in private_paths():
        try:
            if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
                path.chmod(mode)
                logger.info("Restricted permissions on %s", path)
        except OSError as exc:
            logger.warning("Could not restrict permissions on %s: %s", path, exc)
    root = Path(settings.backup_root).resolve()
    if root.is_dir():
        try:
            for p in root.rglob("*"):
                if p.is_symlink():
                    continue
                mode = stat.S_IMODE(p.stat().st_mode)
                if mode & 0o077:
                    p.chmod(0o700 if p.is_dir() else 0o600)
        except OSError as exc:
            logger.warning("Could not restrict backup file permissions: %s", exc)


def startup_warnings():
    from app.core.crypto import check_key
    msgs = []
    if cfg.secret_key_source != "env":
        msgs.append(f"SECRET_KEY in .env is missing or weak; using a generated key from {cfg.SECRET_FILE}.")
    err = check_key()
    if err:
        msgs.append(err)
    for m in msgs:
        logger.warning("!!! %s", m)


def _perm_check(label: str, path: Path) -> dict:
    if os.name != "posix":
        return {"id": f"perm:{label}", "title": f"{label} permissions", "status": "info",
                "detail": "Not checked on this operating system."}
    if not path.exists():
        return {"id": f"perm:{label}", "title": f"{label} permissions", "status": "info",
                "detail": f"{path} does not exist."}
    mode = stat.S_IMODE(path.stat().st_mode)
    ok = not (mode & 0o077)
    return {"id": f"perm:{label}", "title": f"{label} is private", "status": "ok" if ok else "warning",
            "detail": f"{path} has mode {mode:o}." + ("" if ok else " Other users on the server can read it. "
                      f"Run: chmod {'700' if path.is_dir() else '600'} {path}")}


def checks(db, request) -> list[dict]:
    from app.core.crypto import check_key
    from app.core.security import verify_password
    from app.models.user import User
    from app.services import settings_store as store
    out = []

    out.append({"id": "secret_key", "title": "Token signing key", "status":
                "ok" if cfg.secret_key_source in ("env", "generated") else "warning",
                "detail": {"env": "A strong SECRET_KEY is set in .env.",
                           "generated": f"The SECRET_KEY in .env was weak, so NCM uses a random key kept in "
                                        f"{cfg.SECRET_FILE}. Keep that file with your backups of the server.",
                           "temporary": "The SECRET_KEY in .env is weak and NCM could not save a generated key, so "
                                        "everyone is signed out at every restart. Set a long random SECRET_KEY in .env."}
                [cfg.secret_key_source]})

    err = check_key()
    out.append({"id": "encryption_key", "title": "Device password encryption key",
                "status": "critical" if err else "ok",
                "detail": err or "DEVICE_ENCRYPTION_KEY is valid. Device passwords and cloud tokens are encrypted."})

    admin = db.query(User).filter(User.username == settings.default_admin_username).first()
    default_pw = bool(admin and settings.default_admin_password
                      and verify_password(settings.default_admin_password, admin.password_hash))
    out.append({"id": "default_admin", "title": "Default admin password changed",
                "status": "critical" if default_pw else "ok",
                "detail": f"The '{admin.username}' account still uses DEFAULT_ADMIN_PASSWORD from .env. Change it now "
                          f"in My profile." if default_pw else "The first admin account no longer uses the password "
                          "from .env. You can remove DEFAULT_ADMIN_PASSWORD from .env."})

    weak = db.query(User).filter(User.must_change_password.is_(True), User.is_active.is_(True)).all()
    out.append({"id": "weak_passwords", "title": "Users with a password to change",
                "status": "warning" if weak else "ok",
                "detail": ("These users must set a new password at their next sign-in: "
                           + ", ".join(u.username for u in weak[:20])) if weak else
                          "No active user is waiting to change a weak or temporary password."})

    supers = db.query(User).filter(User.role == "super_admin", User.is_active.is_(True)).count()
    out.append({"id": "super_admins", "title": "Number of Super Admins",
                "status": "ok" if 1 <= supers <= 3 else "warning",
                "detail": f"{supers} active Super Admin account(s). Keep this small (1 to 3) and give others the "
                          f"Admin or Operator role."})

    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    https = proto == "https"
    out.append({"id": "https", "title": "HTTPS", "status": "ok" if https else "warning",
                "detail": "This page was opened over HTTPS." if https else
                          "NCM is being used over plain HTTP, so passwords and session tokens cross the network "
                          "unencrypted. Put NCM behind a reverse proxy with a certificate (nginx or Caddy), and "
                          "allow port 8000 only from that proxy."})

    out.append({"id": "lockout", "title": "Sign-in lockout", "status": "ok",
                "detail": f"A username is locked for {settings.login_lockout_minutes} min after "
                          f"{settings.login_max_attempts} failed sign-ins; one address after "
                          f"{settings.login_max_attempts * 4}."})
    idle = settings.session_idle_minutes
    out.append({"id": "idle", "title": "Idle sign-out", "status": "ok" if 0 < idle <= 60 else "warning",
                "detail": f"Browsers sign out after {idle} min without activity. Sessions end after "
                          f"{settings.access_token_expire_minutes} min in any case." if idle else
                          "Idle sign-out is off (SESSION_IDLE_MINUTES=0). Set it to 30 or less."})
    out.append({"id": "docs", "title": "API docs hidden", "status": "warning" if settings.enable_api_docs else "ok",
                "detail": "/docs and /redoc are on (ENABLE_API_DOCS=true). Turn them off when not needed."
                if settings.enable_api_docs else "/docs, /redoc and /openapi.json are switched off."})

    for label, path in (("The .env file", cfg.ENV_FILE), ("The backup folder", Path(settings.backup_root).resolve()),
                        ("The database file", _db_file())):
        if path is not None:
            out.append(_perm_check(label, path))

    live = store.get(db, "ai.live_commands", "0") == "1"
    out.append({"id": "ai_live", "title": "AI live commands", "status": "info",
                "detail": "The AI assistant may run read-only show/display commands on devices. Only users with "
                          "ai.run can use it." if live else "The AI assistant cannot run commands on devices."})
    mask = store.get(db, "ai.mask_ips", "0") == "1"
    out.append({"id": "ai_mask", "title": "IP addresses hidden from AI", "status": "ok" if mask else "info",
                "detail": "IP addresses are replaced with placeholders before text is sent to an AI provider."
                if mask else "Secrets are always masked before text goes to an AI provider, but IP addresses are "
                             "sent as they are. Turn on 'Hide IP addresses' on the AI review page if needed."})
    return out
