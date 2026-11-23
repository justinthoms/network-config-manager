# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Add devices.protocol (ssh/telnet).

Revision ID: 0002_device_protocol
Revises: 0001_baseline
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_device_protocol"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("devices")}
    if "protocol" not in cols:
        op.execute("ALTER TABLE devices ADD COLUMN protocol VARCHAR(10) NOT NULL DEFAULT 'ssh'")


def downgrade():
    raise NotImplementedError
