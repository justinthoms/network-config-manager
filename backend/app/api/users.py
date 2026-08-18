from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from passlib.context import CryptContext

from app.api.deps import current_user, require_permission
from app.core.database import get_db
from app.core.rbac import ROLE_PERMISSIONS
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services.audit import record_audit

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@router.get("/", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), user: User = Depends(require_permission("users.view"))):
    return db.query(User).order_by(User.username).all()

@router.get("/roles")
def list_roles(user: User = Depends(require_permission("users.view"))):
    return {role: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()}

@router.post("/", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db),
                current: User = Depends(require_permission("users.create"))):
    if payload.role not in ROLE_PERMISSIONS:
        raise HTTPException(400, "Invalid role")
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(409, "Username already exists")
    if payload.role == "super_admin" and current.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can create Super Admin users")
    user = User(
        username=payload.username, display_name=payload.display_name, email=payload.email,
        password_hash=pwd_context.hash(payload.password), role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user); db.commit(); db.refresh(user)
    record_audit(db, current, "USER_CREATE", "user", user.id, details=f"username={user.username};role={user.role}")
    return user

@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: Session = Depends(get_db),
                current: User = Depends(require_permission("users.edit"))):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(404, "User not found")
    if payload.role is not None:
        if payload.role not in ROLE_PERMISSIONS: raise HTTPException(400, "Invalid role")
        if payload.role == "super_admin" and current.role != "super_admin":
            raise HTTPException(403, "Only Super Admin can assign Super Admin")
        if user.id == current.id and payload.role != "super_admin":
            raise HTTPException(400, "You cannot remove your own Super Admin role")
        user.role = payload.role
    if payload.display_name is not None: user.display_name = payload.display_name
    if payload.email is not None: user.email = payload.email
    if payload.password: user.password_hash = pwd_context.hash(payload.password)
    if payload.is_active is not None:
        if user.id == current.id and not payload.is_active:
            raise HTTPException(400, "You cannot disable your own account")
        user.is_active = payload.is_active
    db.commit(); db.refresh(user)
    record_audit(db, current, "USER_UPDATE", "user", user.id, details=f"username={user.username};role={user.role}")
    return user

@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, db: Session = Depends(get_db),
                current: User = Depends(require_permission("users.delete"))):
    if user_id == current.id: raise HTTPException(400, "You cannot delete your own account")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(404, "User not found")
    if user.role == "super_admin" and current.role != "super_admin":
        raise HTTPException(403, "Only Super Admin can delete Super Admin")
    name = user.username
    db.delete(user); db.commit()
    record_audit(db, current, "USER_DELETE", "user", user_id, details=f"username={name}")
