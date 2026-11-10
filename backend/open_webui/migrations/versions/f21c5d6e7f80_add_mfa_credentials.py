# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Add encrypted MFA credential storage.

Revision ID: f21c5d6e7f80
Revises: f20b4c5d6e7f
"""

from alembic import op
import sqlalchemy as sa


revision = "f21c5d6e7f80"
down_revision = "f20b4c5d6e7f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mfa_credential",
        sa.Column("user_id", sa.String(), nullable=False, primary_key=True),
        sa.Column("secret", sa.Text(), nullable=True),
        sa.Column("pending_secret", sa.Text(), nullable=True),
        sa.Column("recovery_codes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("last_totp_step", sa.BigInteger(), nullable=False, server_default="-1"),
        sa.Column("challenge_nonce", sa.String(), nullable=True),
        sa.Column("challenge_step", sa.String(), nullable=True),
        sa.Column("challenge_expires_at", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    )


def downgrade():
    op.drop_table("mfa_credential")
