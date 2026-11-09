"""Add external knowledge connections.

Revision ID: f18e2f3a4b5c
Revises: f17d1e2f3a4b
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "f18e2f3a4b5c"
down_revision = "f17d1e2f3a4b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_knowledge_connection",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("auth_config_encrypted", sa.Text(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("health", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "ix_external_knowledge_connection_created_by",
        "external_knowledge_connection",
        ["created_by"],
    )


def downgrade():
    op.drop_index(
        "ix_external_knowledge_connection_created_by",
        table_name="external_knowledge_connection",
    )
    op.drop_table("external_knowledge_connection")
