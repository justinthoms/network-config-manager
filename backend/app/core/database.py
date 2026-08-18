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

def _sqlite_add_column_if_missing(table, column, definition):
    if not settings.database_url.startswith("sqlite"):
        return
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

    Base.metadata.create_all(bind=engine)

    # Safe migration for existing P1-P7 SQLite databases.
    _sqlite_add_column_if_missing("users", "display_name", "VARCHAR(150)")
    _sqlite_add_column_if_missing("users", "email", "VARCHAR(255)")

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
            # Preserve the existing login while giving the initial administrator
            # the full P8 permission set.
            admin.role = "super_admin"
            db.commit()
    finally:
        db.close()
