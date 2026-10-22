# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from app.core.database import Base
from app.core.timeutil import utcnow

class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    hostname = Column(String(255), unique=True, nullable=False, index=True)
    management_ip = Column(String(100), nullable=False)
    vendor = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    username = Column(String(100), nullable=False)
    password_encrypted = Column(String(500), nullable=False)
    ssh_port = Column(Integer, default=22)
    site = Column(String(100), default="DEFAULT")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)
