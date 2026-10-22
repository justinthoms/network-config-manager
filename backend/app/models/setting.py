from sqlalchemy import Column, String, Text, DateTime
from app.core.database import Base
from app.core.timeutil import utcnow


class AppSetting(Base):
    """Simple key/value store for runtime settings (e.g. Google Drive link)."""
    __tablename__ = "app_settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
