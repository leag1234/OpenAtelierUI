"""Add persistent tool activation state.

Revision ID: f19a3b4c5d6e
Revises: f18e2f3a4b5c
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "f19a3b4c5d6e"
down_revision = "f18e2f3a4b5c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tool",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column("tool", "is_active")
