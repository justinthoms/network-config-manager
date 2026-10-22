# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.core.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(150), nullable=True)
    email = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="viewer", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    # Added in v0.9 (security)
    token_version = Column(Integer, default=0, nullable=False)      # +1 signs out every session
    must_change_password = Column(Boolean, default=False, nullable=False)
    password_changed_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(64), nullable=True)
