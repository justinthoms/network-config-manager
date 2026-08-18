from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime
from app.core.database import Base


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"
    __table_args__ = (UniqueConstraint("device_id", name="uq_backup_schedule_device"),)

    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    enabled = Column(Boolean, default=True, nullable=False)
    interval_minutes = Column(Integer, default=360, nullable=False)
    retry_count = Column(Integer, default=2, nullable=False)
    retry_delay_seconds = Column(Integer, default=30, nullable=False)
    retention_count = Column(Integer, default=30, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
