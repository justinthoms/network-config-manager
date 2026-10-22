# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Baseline: the schema of NCM 2.0.

Works on a new, empty database and on databases made by earlier NCM versions
(which created tables at startup and added columns one by one): it only creates
what is missing.

Revision ID: 0001_baseline
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# Columns added after the first releases; old databases may not have them yet.
LATER_COLUMNS = [
    ("users", "display_name", "VARCHAR(150)"),
    ("users", "email", "VARCHAR(255)"),
    ("backups", "size_bytes", "INTEGER"),
    ("backups", "sha256", "VARCHAR(64)"),
    ("backups", "gdrive_file_id", "VARCHAR(200)"),
    ("backups", "gdrive_synced_at", "TIMESTAMP"),
    ("backups", "onedrive_item_id", "VARCHAR(200)"),
    ("backups", "onedrive_synced_at", "TIMESTAMP"),
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "must_change_password", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("users", "password_changed_at", "TIMESTAMP"),
    ("users", "last_login_at", "TIMESTAMP"),
    ("users", "last_login_ip", "VARCHAR(64)"),
]


def upgrade():
    from app.core.database import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)          # creates only missing tables
    inspector = sa.inspect(bind)
    for table, column, definition in LATER_COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def downgrade():
    raise NotImplementedError("The baseline cannot be downgraded")
