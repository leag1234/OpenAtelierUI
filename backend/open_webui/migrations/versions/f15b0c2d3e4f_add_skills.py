"""Add reusable Skills storage.

Revision ID: f15b0c2d3e4f
Revises: e14a0f1c2d3e
"""

from alembic import op
import sqlalchemy as sa


revision = "f15b0c2d3e4f"
down_revision = "e14a0f1c2d3e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skill",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version_id", sa.String(), nullable=True),
        sa.Column("access_grants", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_skill_user_id", "skill", ["user_id"])


def downgrade():
    op.drop_index("ix_skill_user_id", table_name="skill")
    op.drop_table("skill")
