"""Alembic environment: uses NCM's own engine and models (DATABASE_URL from .env)."""
from alembic import context

from app.core.database import Base, engine
import app.models  # noqa: F401  (registers every table on Base.metadata)

target_metadata = Base.metadata


def run_migrations_online():
    connection = context.config.attributes.get("connection")
    if connection is not None:          # called from init_db() with an open connection
        context.configure(connection=connection, target_metadata=target_metadata,
                          render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as conn:      # called from the alembic command line
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) migrations are not supported; run against the database.")
run_migrations_online()
