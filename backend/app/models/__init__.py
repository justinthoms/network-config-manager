# Importing the package registers every table on Base.metadata (used by init_db and Alembic).
from app.models.user import User  # noqa: F401
from app.models.device import Device  # noqa: F401
from app.models.backup import Backup  # noqa: F401
from app.models.schedule import BackupSchedule  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.setting import AppSetting  # noqa: F401
from app.models.ai_review import AIReview  # noqa: F401
from app.models.ai_provider import AIProvider  # noqa: F401
from app.models.health import HealthCheck  # noqa: F401
