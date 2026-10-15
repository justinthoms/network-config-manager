from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.core.security import ALGORITHM
from app.core.rbac import has_permission
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# What a user who must change their password may still call.
_PASSWORD_CHANGE_ALLOWED = {("GET", "/api/auth/me"), ("PATCH", "/api/auth/me"), ("POST", "/api/auth/logout-all")}


def current_user(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username or payload.get("typ", "access") != "access":
            raise HTTPException(401, "Invalid token")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(401, "User inactive or not found")
    if int(payload.get("ver", 0)) != int(user.token_version or 0):
        raise HTTPException(401, "Session ended. Sign in again.")
    if user.must_change_password and (request.method, request.url.path.rstrip("/")) not in _PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(403, "password_change_required")
    return user


def require_permission(permission: str):
    def checker(user=Depends(current_user)):
        if not has_permission(user.role, permission):
            raise HTTPException(403, f"Permission denied: {permission}")
        return user
    return checker


def require_roles(*roles):
    def checker(user=Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user
    return checker
