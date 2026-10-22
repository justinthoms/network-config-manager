# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings

_sqlite = settings.database_url.startswith("sqlite")
# timeout: with many backups in parallel, SQLite writers wait for each other instead of failing
connect_args = {"check_same_thread": False, "timeout": 30} if _sqlite else {}
# Room for parallel backups, health checks and web requests at the same time
_workers = max(1, min(int(settings.backup_workers or 10), 50))
engine = create_engine(settings.database_url, connect_args=connect_args,
                       pool_size=max(5, _workers), max_overflow=_workers + 10)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def startup_lock():
    """Worker processes starting at the same time run first-start setup one after another."""
    from app.core.config import BASE_DIR
    try:
        import fcntl
    except ImportError:          # no flock (Windows): a single process is assumed
        yield
        return
    with open(BASE_DIR / ".startup.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


def migrate():
    """Bring the database schema up to date with Alembic (backend/migrations)."""
    from alembic import command
    from alembic.config import Config
    from app.core.config import BASE_DIR

    cfg = Config()
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def init_db():
    from app.models import User

    migrate()

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
