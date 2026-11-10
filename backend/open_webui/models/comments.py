# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Persistent collaborative comments and reactions."""

import time
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, JSON, String, Text, UniqueConstraint

from open_webui.internal.db import Base, get_db


class Comment(Base):
    __tablename__ = "comment"

    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=True, index=True)
    resource_type = Column(String, nullable=False, index=True)
    resource_id = Column(String, nullable=False, index=True)
    parent_id = Column(String, nullable=True, index=True)
    reply_to_id = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    target_type = Column(String, nullable=True)
    target_id = Column(String, nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False, index=True)
    updated_at = Column(BigInteger, nullable=False)
    deleted_at = Column(BigInteger, nullable=True)


class CommentReaction(Base):
    __tablename__ = "comment_reaction"
    __table_args__ = (UniqueConstraint("comment_id", "user_id", "name"),)

    id = Column(String, primary_key=True)
    comment_id = Column(String, nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class CommentModel(BaseModel):
    id: str
    user_id: Optional[str] = None
    resource_type: str
    resource_id: str
    parent_id: Optional[str] = None
    reply_to_id: Optional[str] = None
    content: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    meta: Optional[dict] = None
    created_at: int
    updated_at: int
    deleted_at: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


def _model(row):
    return CommentModel.model_validate(row) if row is not None else None


class CommentTable:
    def create(self, user_id, form, *, parent=None):
        now = time.time_ns()
        comment_id = str(form.temp_id) if getattr(form, "temp_id", None) else str(uuid.uuid4())
        row = Comment(
            id=comment_id,
            user_id=user_id,
            resource_type=parent.resource_type if parent else form.resource_type,
            resource_id=parent.resource_id if parent else form.resource_id,
            parent_id=parent.id if parent else None,
            reply_to_id=getattr(form, "reply_to_id", None),
            content=form.content,
            target_type=getattr(form, "target_type", None),
            target_id=getattr(form, "target_id", None),
            meta={"anchor": form.anchor.model_dump()} if getattr(form, "anchor", None) else None,
            created_at=now,
            updated_at=now,
        )
        with get_db() as db:
            if db.get(Comment, comment_id) is not None:
                return None
            db.add(row)
            db.commit()
            db.refresh(row)
            return _model(row)

    def get(self, comment_id):
        with get_db() as db:
            return _model(db.get(Comment, comment_id))

    def list_roots(self, resource_type, resource_id, cursor=None, limit=20, anchored=False):
        with get_db() as db:
            base = db.query(Comment).filter_by(
                resource_type=resource_type, resource_id=resource_id, parent_id=None
            )
            total = base.count()
            query = base
            if anchored:
                query = query.filter(Comment.meta.isnot(None))
            if cursor:
                try:
                    timestamp, comment_id = cursor.split(":", 1)
                    timestamp = int(timestamp)
                    query = query.filter(
                        (Comment.created_at > timestamp)
                        | ((Comment.created_at == timestamp) & (Comment.id > comment_id))
                    )
                except (TypeError, ValueError):
                    return None, total
            rows = query.order_by(Comment.created_at.asc(), Comment.id.asc()).limit(limit + 1).all()
            more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = f"{rows[-1].created_at}:{rows[-1].id}" if more and rows else None
            return [_model(row) for row in rows], total, next_cursor

    def replies(self, parent_id, cursor=None, limit=50):
        with get_db() as db:
            query = db.query(Comment).filter_by(parent_id=parent_id)
            if cursor:
                try:
                    timestamp, comment_id = cursor.split(":", 1)
                    timestamp = int(timestamp)
                    query = query.filter(
                        (Comment.created_at > timestamp)
                        | ((Comment.created_at == timestamp) & (Comment.id > comment_id))
                    )
                except (TypeError, ValueError):
                    return None, None
            rows = query.order_by(Comment.created_at.asc(), Comment.id.asc()).limit(limit + 1).all()
            more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = f"{rows[-1].created_at}:{rows[-1].id}" if more and rows else None
            return [_model(row) for row in rows], next_cursor

    def reply_summary(self, comment_id):
        with get_db() as db:
            rows = (
                db.query(Comment)
                .filter_by(parent_id=comment_id, deleted_at=None)
                .order_by(Comment.created_at.desc())
                .all()
            )
            return len(rows), rows[0].created_at if rows else None

    def update(self, comment_id, content, expected_etag):
        with get_db() as db:
            row = db.get(Comment, comment_id)
            if row is None:
                return None, "missing"
            if str(row.updated_at) != expected_etag:
                return _model(row), "conflict"
            row.content = content
            row.updated_at = time.time_ns()
            db.commit()
            db.refresh(row)
            return _model(row), None

    def soft_delete(self, comment_id, expected_etag):
        with get_db() as db:
            row = db.get(Comment, comment_id)
            if row is None:
                return None, "missing"
            if str(row.updated_at) != expected_etag:
                return _model(row), "conflict"
            now = time.time_ns()
            row.content = ""
            row.user_id = None
            row.deleted_at = now
            row.updated_at = now
            db.query(CommentReaction).filter_by(comment_id=comment_id).delete()
            db.commit()
            db.refresh(row)
            return _model(row), None

    def reactions(self, comment_id):
        with get_db() as db:
            return [
                {"user_id": row.user_id, "name": row.name}
                for row in db.query(CommentReaction)
                .filter_by(comment_id=comment_id)
                .order_by(CommentReaction.created_at.asc(), CommentReaction.id.asc())
                .all()
            ]

    def add_reaction(self, comment_id, user_id, name):
        with get_db() as db:
            if db.query(CommentReaction).filter_by(
                comment_id=comment_id, user_id=user_id, name=name
            ).first() is None:
                db.add(CommentReaction(
                    id=str(uuid.uuid4()), comment_id=comment_id, user_id=user_id,
                    name=name, created_at=time.time_ns(),
                ))
                db.commit()
        return True

    def remove_reaction(self, comment_id, user_id, name):
        with get_db() as db:
            db.query(CommentReaction).filter_by(
                comment_id=comment_id, user_id=user_id, name=name
            ).delete()
            db.commit()
        return True


Comments = CommentTable()
