from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.core.security import ALGORITHM
from app.core.rbac import has_permission
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(401, "Invalid token")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(401, "User inactive or not found")
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
