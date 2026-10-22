# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from app.core.database import Base
from app.core.timeutil import utcnow


class HealthCheck(Base):
    """One run of read-only health commands on a device."""
    __tablename__ = "health_checks"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False)          # ok | warning | critical | failed
    trigger = Column(String(20), default="manual")       # manual | scheduled
    cpu = Column(Integer, nullable=True)                 # percent
    memory = Column(Integer, nullable=True)              # percent
    summary = Column(Text, nullable=True)                # JSON: facts, interfaces, issues, log counts
    logs = Column(Text, nullable=True)                   # JSON list of parsed log entries
    outputs = Column(Text, nullable=True)                # JSON list of {command, output, ok, error}
    ai = Column(Text, nullable=True)                     # JSON AI summary
    error = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utcnow, index=True)
