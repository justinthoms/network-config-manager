from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from app.core.database import Base


class AIReview(Base):
    """Cached Gemini result for a config validation or a change review."""
    __tablename__ = "ai_reviews"
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False)            # "validate" | "review"
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    backup_a = Column(Integer, nullable=True)             # older backup (review only)
    backup_b = Column(Integer, nullable=False)            # backup validated / newer backup
    model = Column(String(100), nullable=True)
    risk = Column(String(20), nullable=True)              # review: low|medium|high|critical
    score = Column(Integer, nullable=True)                # validate: 0-100
    result = Column(Text, nullable=False)                 # JSON
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
