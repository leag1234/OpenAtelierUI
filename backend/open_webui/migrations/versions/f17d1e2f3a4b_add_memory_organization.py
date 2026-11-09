"""Add compatible memory organization fields.

Revision ID: f17d1e2f3a4b
Revises: f16c0d3e4f5a
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "f17d1e2f3a4b"
down_revision = "f16c0d3e4f5a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory", sa.Column("path", sa.Text(), nullable=True))
    op.add_column(
        "memory",
        sa.Column("type", sa.String(), nullable=False, server_default="context"),
    )
    op.add_column("memory", sa.Column("meta", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("memory", "meta")
    op.drop_column("memory", "type")
    op.drop_column("memory", "path")
