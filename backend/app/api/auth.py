import math
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rbac import ROLE_PERMISSIONS
from app.core.security import (verify_password, create_access_token, hash_password, password_problem,
                               pwd_context, login_limiter)
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit import record_audit

router = APIRouter()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    source = _ip(request)
    name = (data.username or "").strip()
    wait = login_limiter.locked_for(name, source)
    if wait:
        record_audit(db, None, "LOGIN", "auth", name[:100], "BLOCKED", "Too many failed sign-ins", source)
        raise HTTPException(429, f"Too many failed sign-ins. Try again in {math.ceil(wait / 60)} minute(s).")
    user = db.query(User).filter(User.username == name).first()
    ok = verify_password(data.password, user.password_hash if user else None)
    if not user or not user.is_active or not ok:
        locked = login_limiter.failed(name, source)
        record_audit(db, user, "LOGIN", "auth", name[:100], "FAILED",
                     "Invalid credentials or inactive user"
                     + (f"; locked for {settings.login_lockout_minutes} min" if locked else ""), source)
        raise HTTPException(401, "Invalid username or password")
    login_limiter.success(name)
    if password_problem(data.password, user.username):
        user.must_change_password = True
    if pwd_context.needs_update(user.password_hash):
        user.password_hash = hash_password(data.password)
    user.last_login_at, user.last_login_ip = datetime.utcnow(), source
    db.commit()
    record_audit(db, user, "LOGIN", "auth", user.username, "SUCCESS",
                 "password change required" if user.must_change_password else None, source)
    return TokenResponse(access_token=create_access_token(user.username, user.token_version))


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _me(user)


def _me(user: User) -> dict:
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name,
        "email": user.email, "role": user.role, "is_active": user.is_active,
        "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
        "must_change_password": bool(user.must_change_password),
        "session_idle_minutes": max(0, settings.session_idle_minutes),
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "password_changed_at": user.password_changed_at.isoformat() if user.password_changed_at else None,
    }


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=150)
    email: str | None = Field(default=None, max_length=255)
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str | None = Field(default=None, max_length=200)


@router.patch("/me")
def update_me(data: ProfileIn, request: Request, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    u = db.get(User, user.id)
    if data.display_name is not None:
        u.display_name = data.display_name.strip() or None
    if data.email is not None:
        u.email = data.email.strip() or None
    new_token = None
    if data.new_password:
        if not data.current_password or not verify_password(data.current_password, u.password_hash):
            record_audit(db, u, "PASSWORD_CHANGE", "user", u.id, "FAILED", "wrong current password", _ip(request))
            raise HTTPException(400, "Current password is wrong")
        problem = password_problem(data.new_password, u.username)
        if problem:
            raise HTTPException(400, problem)
        if verify_password(data.new_password, u.password_hash):
            raise HTTPException(400, "The new password must be different from the current one")
        u.password_hash = hash_password(data.new_password)
        u.must_change_password = False
        u.password_changed_at = datetime.utcnow()
        u.token_version = (u.token_version or 0) + 1      # sign out every other session
        new_token = create_access_token(u.username, u.token_version)
    db.commit()
    record_audit(db, u, "PROFILE_UPDATE", "user", u.id, details="password changed" if data.new_password else None,
                 source_ip=_ip(request))
    out = _me(u)
    if new_token:
        out["access_token"] = new_token
    return out


@router.post("/logout-all")
def logout_all(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, user.id)
    u.token_version = (u.token_version or 0) + 1
    db.commit()
    record_audit(db, u, "LOGOUT_ALL", "user", u.id, source_ip=_ip(request))
    return {"success": True}
