from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _add_column_if_missing(table, column, definition):
    """Lightweight forward-only migration (works for SQLite and PostgreSQL)."""
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns(table)}
    if column not in columns:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))

def init_db():
    # Import every model before create_all.
    from app.models.user import User
    from app.models.device import Device
    from app.models.backup import Backup
    from app.models.schedule import BackupSchedule
    from app.models.audit import AuditLog
    from app.models.setting import AppSetting  # noqa: F401
    from app.models.ai_review import AIReview  # noqa: F401
    from app.models.ai_provider import AIProvider  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Safe migrations for existing databases.
    _add_column_if_missing("users", "display_name", "VARCHAR(150)")
    _add_column_if_missing("users", "email", "VARCHAR(255)")
    _add_column_if_missing("backups", "size_bytes", "INTEGER")
    _add_column_if_missing("backups", "sha256", "VARCHAR(64)")
    _add_column_if_missing("backups", "gdrive_file_id", "VARCHAR(200)")
    _add_column_if_missing("backups", "gdrive_synced_at", "TIMESTAMP")

    db = SessionLocal()
    try:
        from app.core.security import hash_password
        admin = db.query(User).filter(User.username == settings.default_admin_username).first()
        if not admin:
            db.add(User(
                username=settings.default_admin_username,
                display_name="Administrator",
                password_hash=hash_password(settings.default_admin_password),
                role="super_admin",
                is_active=True,
            ))
            db.commit()
        elif admin.role == "admin" and admin.username == settings.default_admin_username:
            admin.role = "super_admin"
            db.commit()
    finally:
        db.close()
