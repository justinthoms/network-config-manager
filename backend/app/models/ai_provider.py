from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from app.core.database import Base


class AIProvider(Base):
    """An AI provider connection (API key + endpoint). Shared when owner_id is NULL, personal otherwise."""
    __tablename__ = "ai_providers"
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    kind = Column(String(30), nullable=False)          # gemini | openai | openrouter | groq | mistral | anthropic | ollama | custom
    base_url = Column(String(300), nullable=True)
    api_key_encrypted = Column(Text, nullable=True)
    default_model = Column(String(200), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
