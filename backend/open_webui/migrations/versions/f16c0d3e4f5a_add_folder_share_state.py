"""Add persistent local folder-sharing state.

Revision ID: f16c0d3e4f5a
Revises: f15b0c2d3e4f
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "f16c0d3e4f5a"
down_revision = "f15b0c2d3e4f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "folder_share_state",
        sa.Column("folder_id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("access_grants", sa.JSON(), nullable=False),
        sa.Column("read_state", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("folder_share_state")
