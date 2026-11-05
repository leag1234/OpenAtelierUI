"""Add nullable folder data.

Revision ID: 9f0c1a2b3d4e
Revises: 3781e22d8b01
Create Date: 2026-11-05 19:00:00.000000
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "9f0c1a2b3d4e"
down_revision = "3781e22d8b01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("folder", sa.Column("data", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("folder", "data")
