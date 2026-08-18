from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from app.core.database import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(100), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource = Column(String(100), nullable=True)
    resource_id = Column(String(100), nullable=True)
    status = Column(String(30), nullable=False, default="SUCCESS")
    details = Column(Text, nullable=True)
    source_ip = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
