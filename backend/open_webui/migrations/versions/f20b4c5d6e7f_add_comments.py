"""Add collaborative comments and reactions.

Revision ID: f20b4c5d6e7f
Revises: f19a3b4c5d6e
"""

# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from alembic import op
import sqlalchemy as sa


revision = "f20b4c5d6e7f"
down_revision = "f19a3b4c5d6e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "comment",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("reply_to_id", sa.String(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=True),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
    )
    for name, columns in (
        ("ix_comment_user_id", ["user_id"]),
        ("ix_comment_resource_type", ["resource_type"]),
        ("ix_comment_resource_id", ["resource_id"]),
        ("ix_comment_parent_id", ["parent_id"]),
        ("ix_comment_created_at", ["created_at"]),
    ):
        op.create_index(name, "comment", columns)
    op.create_table(
        "comment_reaction",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("comment_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("comment_id", "user_id", "name"),
    )
    op.create_index("ix_comment_reaction_comment_id", "comment_reaction", ["comment_id"])
    op.create_index("ix_comment_reaction_user_id", "comment_reaction", ["user_id"])


def downgrade():
    op.drop_index("ix_comment_reaction_user_id", table_name="comment_reaction")
    op.drop_index("ix_comment_reaction_comment_id", table_name="comment_reaction")
    op.drop_table("comment_reaction")
    for name in (
        "ix_comment_created_at", "ix_comment_parent_id", "ix_comment_resource_id",
        "ix_comment_resource_type", "ix_comment_user_id",
    ):
        op.drop_index(name, table_name="comment")
    op.drop_table("comment")
