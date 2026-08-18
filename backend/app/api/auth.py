from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.api.deps import current_user
from app.core.database import get_db
from app.core.security import verify_password, create_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.services.audit import record_audit

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
    }
