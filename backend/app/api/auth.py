from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.security import verify_password, create_access_token, hash_password
from pydantic import BaseModel, Field
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit import record_audit
from app.core.rbac import ROLE_PERMISSIONS

router = APIRouter()

@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    source = request.client.host if request.client else None
    if not user or not user.is_active or not verify_password(data.password, user.password_hash):
        record_audit(db, user, "LOGIN", "auth", data.username, "FAILED", "Invalid credentials or inactive user", source)
        raise HTTPException(401, "Invalid username or password")
    record_audit(db, user, "LOGIN", "auth", user.username, "SUCCESS", None, source)
    return TokenResponse(access_token=create_access_token(user.username))

@router.get("/me")
def me(user: User = Depends(current_user)):
    return {
        "id": user.id, "username": user.username, "display_name": user.display_name,
        "email": user.email, "role": user.role, "is_active": user.is_active,
        "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set())),
    }



class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=150)
    email: str | None = Field(default=None, max_length=255)
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=8, max_length=200)


@router.patch("/me")
def update_me(data: ProfileIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    u = db.get(User, user.id)
    if data.display_name is not None:
        u.display_name = data.display_name.strip() or None
    if data.email is not None:
        u.email = data.email.strip() or None
    if data.new_password:
        if not data.current_password or not verify_password(data.current_password, u.password_hash):
            raise HTTPException(400, "Current password is wrong")
        u.password_hash = hash_password(data.new_password)
    db.commit()
    record_audit(db, u, "PROFILE_UPDATE", "user", u.id, details="password changed" if data.new_password else None)
    return me(u)
