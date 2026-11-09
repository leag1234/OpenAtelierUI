"""Add stable prompt identifiers and version history.

Revision ID: e14a0f1c2d3e
Revises: 9f0c1a2b3d4e
"""

import uuid

from alembic import op
import sqlalchemy as sa


revision = "e14a0f1c2d3e"
down_revision = "9f0c1a2b3d4e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("prompt", sa.Column("uuid", sa.String(), nullable=True))
    op.add_column("prompt", sa.Column("data", sa.JSON(), nullable=True))
    op.add_column("prompt", sa.Column("meta", sa.JSON(), nullable=True))
    op.add_column("prompt", sa.Column("tags", sa.JSON(), nullable=True))
    op.add_column("prompt", sa.Column("version_id", sa.String(), nullable=True))
    op.add_column("prompt", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.add_column("prompt", sa.Column("created_at", sa.BigInteger(), nullable=True))
    op.add_column("prompt", sa.Column("updated_at", sa.BigInteger(), nullable=True))
    op.add_column("prompt", sa.Column("access_grants", sa.JSON(), nullable=True))
    op.create_index("ix_prompt_uuid", "prompt", ["uuid"], unique=True)

    op.create_table(
        "prompt_history",
        sa.Column("id", sa.String(), nullable=False, primary_key=True),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_prompt_history_prompt_id", "prompt_history", ["prompt_id"])

    connection = op.get_bind()
    prompt = sa.table(
        "prompt",
        sa.column("command", sa.String()), sa.column("user_id", sa.String()),
        sa.column("title", sa.Text()), sa.column("content", sa.Text()),
        sa.column("access_control", sa.JSON()),
        sa.column("timestamp", sa.BigInteger()), sa.column("uuid", sa.String()),
        sa.column("version_id", sa.String()), sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.BigInteger()), sa.column("updated_at", sa.BigInteger()),
        sa.column("access_grants", sa.JSON()),
    )
    history = sa.table(
        "prompt_history",
        sa.column("id", sa.String()), sa.column("prompt_id", sa.String()),
        sa.column("parent_id", sa.String()), sa.column("snapshot", sa.JSON()),
        sa.column("user_id", sa.String()), sa.column("commit_message", sa.Text()),
        sa.column("created_at", sa.BigInteger()),
    )
    for row in connection.execute(sa.select(
        prompt.c.command, prompt.c.user_id, prompt.c.title,
        prompt.c.content, prompt.c.timestamp, prompt.c.access_control,
    )).mappings():
        prompt_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
        timestamp = int(row["timestamp"] or 0)
        acl = row["access_control"]
        grants = []
        if acl is None:
            grants.append({
                "id": str(uuid.uuid4()), "resource_type": "prompt",
                "resource_id": prompt_id, "principal_type": "user",
                "principal_id": "*", "permission": "read",
                "created_at": timestamp,
            })
        elif isinstance(acl, dict):
            for permission in ("read", "write"):
                values = acl.get(permission, {})
                if not isinstance(values, dict):
                    continue
                for principal_type, key in (("user", "user_ids"), ("group", "group_ids")):
                    identifiers = values.get(key, [])
                    if not isinstance(identifiers, list):
                        continue
                    for principal_id in identifiers:
                        if not isinstance(principal_id, str) or not principal_id:
                            continue
                        grants.append({
                            "id": str(uuid.uuid4()), "resource_type": "prompt",
                            "resource_id": prompt_id, "principal_type": principal_type,
                            "principal_id": principal_id, "permission": permission,
                            "created_at": timestamp,
                        })
        connection.execute(
            prompt.update().where(prompt.c.command == row["command"]).values(
                uuid=prompt_id, version_id=version_id, is_active=True,
                created_at=timestamp, updated_at=timestamp, access_grants=grants,
            )
        )
        connection.execute(history.insert().values(
            id=version_id, prompt_id=prompt_id, parent_id=None,
            snapshot={
                "command": row["command"], "name": row["title"],
                "content": row["content"], "data": None, "meta": None,
                "tags": None, "access_grants": grants,
            },
            user_id=row["user_id"], commit_message="Initial", created_at=timestamp,
        ))


def downgrade():
    op.drop_index("ix_prompt_history_prompt_id", table_name="prompt_history")
    op.drop_table("prompt_history")
    op.drop_index("ix_prompt_uuid", table_name="prompt")
    for column in (
        "access_grants", "updated_at", "created_at", "is_active", "version_id",
        "tags", "meta", "data", "uuid",
    ):
        op.drop_column("prompt", column)
