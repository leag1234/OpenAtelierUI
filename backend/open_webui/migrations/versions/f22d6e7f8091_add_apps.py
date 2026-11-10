# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Add encrypted Apps and channel membership storage.

Revision ID: f22d6e7f8091
Revises: f21c5d6e7f80
"""

from alembic import op
import sqlalchemy as sa


revision = "f22d6e7f8091"
down_revision = "f21c5d6e7f80"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "app",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("configured", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("access_grants", sa.JSON(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("connection_token_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_app_slug"),
    )
    op.create_index("ix_app_slug", "app", ["slug"], unique=True)
    op.create_table(
        "channel_app",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("app_id", sa.String(), primary_key=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_channel_app_channel_id", "channel_app", ["channel_id"])
    op.create_index("ix_channel_app_app_id", "channel_app", ["app_id"])


def downgrade():
    op.drop_index("ix_channel_app_app_id", table_name="channel_app")
    op.drop_index("ix_channel_app_channel_id", table_name="channel_app")
    op.drop_table("channel_app")
    op.drop_index("ix_app_slug", table_name="app")
    op.drop_table("app")
