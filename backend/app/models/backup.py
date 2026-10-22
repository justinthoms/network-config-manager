# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from app.core.database import Base
from app.core.timeutil import utcnow

class Backup(Base):
    __tablename__ = "backups"
    id = Column(Integer, primary_key=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False)
    status = Column(String(30), nullable=False)
    file_path = Column(String(1000), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    # Added in v0.2
    size_bytes = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True)
    gdrive_file_id = Column(String(200), nullable=True)
    gdrive_synced_at = Column(DateTime, nullable=True)
    # Added in v0.7
    onedrive_item_id = Column(String(200), nullable=True)
    onedrive_synced_at = Column(DateTime, nullable=True)
