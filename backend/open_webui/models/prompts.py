# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Prompt persistence shared by legacy command routes and stable-ID routes."""

import time
import uuid
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, JSON, String, Text

from open_webui.internal.db import Base, get_db
from open_webui.models.users import UserResponse, Users


class Prompt(Base):
    __tablename__ = "prompt"

    # The command remains the physical primary key for migration compatibility.
    command = Column(String, primary_key=True)
    user_id = Column(String)
    title = Column(Text)
    content = Column(Text)
    timestamp = Column(BigInteger)
    access_control = Column(JSON, nullable=True)

    # ``prompt.id`` is an inherited integer implementation key in SQLite.
    # Keep it untouched and map the public stable identifier to ``uuid``.
    id = Column("uuid", String, unique=True, index=True, nullable=True)
    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    tags = Column(JSON, nullable=True)
    version_id = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=True, default=True)
    created_at = Column(BigInteger, nullable=True)
    updated_at = Column(BigInteger, nullable=True)
    access_grants = Column(JSON, nullable=True)


class PromptHistory(Base):
    __tablename__ = "prompt_history"

    id = Column(String, primary_key=True)
    prompt_id = Column(String, index=True, nullable=False)
    parent_id = Column(String, nullable=True)
    snapshot = Column(JSON, nullable=False)
    user_id = Column(String, nullable=False)
    commit_message = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)


class AccessGrantModel(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_at: int


class PromptModel(BaseModel):
    id: Optional[str] = None
    command: str
    user_id: str
    name: str
    content: str
    data: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    access_grants: list[AccessGrantModel] = Field(default_factory=list)
    version_id: Optional[str] = None
    is_active: Optional[bool] = True
    updated_at: Optional[int] = None
    created_at: Optional[int] = None


class PromptAccessResponse(PromptModel):
    user: Optional[UserResponse] = None
    write_access: Optional[bool] = False


class PromptHistoryModel(BaseModel):
    id: str
    prompt_id: str
    parent_id: Optional[str] = None
    snapshot: dict[str, Any]
    user_id: str
    commit_message: Optional[str] = None
    created_at: int
    model_config = ConfigDict(from_attributes=True)


class PromptHistoryResponse(PromptHistoryModel):
    """Response model with user info."""

    user: Optional[UserResponse] = None


class PromptForm(BaseModel):
    command: str
    name: str = Field(validation_alias=AliasChoices("name", "title"))
    content: str
    data: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
    tags: Optional[list[str]] = None
    access_grants: Optional[list[dict[str, Any]]] = None
    commit_message: Optional[str] = None
    version_id: Optional[str] = None
    is_production: Optional[bool] = True


class PromptMetadataForm(BaseModel):
    name: str
    command: str
    tags: Optional[list[str]] = None


class PromptVersionUpdateForm(BaseModel):
    version_id: str


class PromptAccessGrantsForm(BaseModel):
    access_grants: list[dict[str, Any]]


# Legacy projection retained for command-keyed integrations.
class PromptResponse(BaseModel):
    command: str
    user_id: str
    title: str
    content: str
    timestamp: int
    access_control: Optional[dict] = None
    data: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    is_active: bool = True
    name: str
    created_at: int
    updated_at: int
    access_grants: list[AccessGrantModel] = Field(default_factory=list)


class PromptUserResponse(PromptResponse):
    user: Optional[UserResponse] = None


def _now() -> int:
    return int(time.time())


def _public(row: Prompt) -> dict[str, Any]:
    created_at = row.created_at if row.created_at is not None else row.timestamp
    updated_at = row.updated_at if row.updated_at is not None else row.timestamp
    return {
        "id": row.id, "command": row.command, "user_id": row.user_id,
        "name": row.title, "content": row.content, "data": row.data,
        "meta": row.meta, "tags": row.tags, "access_grants": row.access_grants or [],
        "version_id": row.version_id,
        "is_active": True if row.is_active is None else row.is_active,
        "updated_at": updated_at, "created_at": created_at,
    }


def _legacy(row: Prompt) -> dict[str, Any]:
    public = _public(row)
    return {
        "command": row.command, "user_id": row.user_id, "title": row.title,
        "content": row.content, "timestamp": row.timestamp,
        "access_control": row.access_control, "data": row.data or {},
        "meta": row.meta or {}, "tags": row.tags or [],
        "is_active": public["is_active"], "name": row.title,
        "created_at": public["created_at"], "updated_at": public["updated_at"],
        "access_grants": row.access_grants or [],
    }


def _snapshot(row: Prompt) -> dict[str, Any]:
    public = _public(row)
    return {key: public[key] for key in (
        "command", "name", "content", "data", "meta", "tags", "access_grants"
    )}


def _normalize_grants(prompt_id: str, grants, now: int) -> list[dict[str, Any]]:
    normalized, seen = [], set()
    for grant in grants or []:
        if not isinstance(grant, dict):
            raise ValueError("invalid_grant")
        principal_type, principal_id = grant.get("principal_type"), grant.get("principal_id")
        permission = grant.get("permission")
        if principal_type not in ("user", "group") or permission not in ("read", "write"):
            raise ValueError("invalid_grant")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("invalid_grant")
        identity = (principal_type, principal_id, permission)
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append({
            "id": str(uuid.uuid4()), "resource_type": "prompt", "resource_id": prompt_id,
            "principal_type": principal_type, "principal_id": principal_id,
            "permission": permission, "created_at": now,
        })
    return normalized


class PromptsTable:
    def _history(self, row: Prompt, user_id: str, commit_message: Optional[str]) -> PromptHistory:
        history = PromptHistory(
            id=str(uuid.uuid4()), prompt_id=row.id, parent_id=row.version_id,
            snapshot=_snapshot(row), user_id=user_id,
            commit_message=commit_message, created_at=_now(),
        )
        row.version_id = history.id
        return history

    def insert_new_prompt(self, user_id: str, form_data: PromptForm) -> Optional[PromptModel]:
        now = _now()
        try:
            with get_db() as db:
                prompt_id = str(uuid.uuid4())
                row = Prompt(
                    id=prompt_id, command=form_data.command, user_id=user_id,
                    title=form_data.name, content=form_data.content, timestamp=now,
                    data=form_data.data, meta=form_data.meta, tags=form_data.tags,
                    access_control={},
                    access_grants=_normalize_grants(prompt_id, form_data.access_grants, now),
                    is_active=True,
                    created_at=now, updated_at=now,
                )
                db.add(row)
                db.flush()
                history = self._history(row, user_id, form_data.commit_message or "Initial")
                history.parent_id = None
                db.add(history)
                db.commit()
                return PromptModel.model_validate(_public(row))
        except Exception:
            return None

    def get_prompt_by_command(self, command: str) -> Optional[PromptResponse]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(command=command).first()
            return PromptResponse.model_validate(_legacy(row)) if row else None

    def get_prompt_by_id(self, prompt_id: str) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            return PromptModel.model_validate(_public(row)) if row else None

    def get_prompts(self) -> list[PromptUserResponse]:
        with get_db() as db:
            result = []
            for row in db.query(Prompt).order_by(Prompt.timestamp.desc()).all():
                user = Users.get_user_by_id(row.user_id)
                result.append(PromptUserResponse.model_validate({
                    **_legacy(row), "user": user.model_dump() if user else None,
                }))
            return result

    def get_prompts_by_user_id(self, user_id: str, permission: str = "write") -> list[PromptUserResponse]:
        return [prompt for prompt in self.get_prompts() if
                prompt.user_id == user_id or self.has_grant(prompt.access_grants, user_id, permission)]

    @staticmethod
    def has_grant(grants, user_id: str, permission: str) -> bool:
        from open_webui.models.groups import Groups

        group_ids = {group.id for group in Groups.get_groups_by_member_id(user_id)}
        for raw in grants or []:
            grant = raw.model_dump() if hasattr(raw, "model_dump") else raw
            if grant.get("permission") != permission:
                continue
            if grant.get("principal_type") == "user" and grant.get("principal_id") in (user_id, "*"):
                return True
            if grant.get("principal_type") == "group" and grant.get("principal_id") in group_ids:
                return True
        return False

    def update_prompt_by_command(self, command: str, form_data: PromptForm) -> Optional[PromptResponse]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(command=command).first()
            if row is None:
                return None
            row.title, row.content = form_data.name, form_data.content
            row.timestamp = row.updated_at = _now()
            db.commit()
            return PromptResponse.model_validate(_legacy(row))

    def update_prompt_by_id(self, prompt_id: str, form_data: PromptForm, user_id: str) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            if row is None:
                return None
            if form_data.command != row.command and db.query(Prompt).filter_by(command=form_data.command).first():
                raise ValueError("command_taken")
            row.command, row.title, row.content = form_data.command, form_data.name, form_data.content
            row.data, row.meta, row.tags = form_data.data, form_data.meta, form_data.tags
            row.timestamp = row.updated_at = _now()
            db.add(self._history(row, user_id, form_data.commit_message))
            db.commit()
            return PromptModel.model_validate(_public(row))

    def update_metadata(self, prompt_id: str, form_data: PromptMetadataForm) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            if row is None:
                return None
            if form_data.command != row.command and db.query(Prompt).filter_by(command=form_data.command).first():
                raise ValueError("command_taken")
            row.command, row.title, row.tags = form_data.command, form_data.name, form_data.tags
            row.timestamp = row.updated_at = _now()
            db.commit()
            return PromptModel.model_validate(_public(row))

    def select_version(self, prompt_id: str, version_id: str) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            history = db.query(PromptHistory).filter_by(id=version_id, prompt_id=prompt_id).first()
            if row is None or history is None:
                return None
            snapshot = history.snapshot
            row.title, row.content = snapshot.get("name", row.title), snapshot.get("content", row.content)
            row.data, row.meta, row.tags = snapshot.get("data"), snapshot.get("meta"), snapshot.get("tags")
            row.version_id, row.timestamp, row.updated_at = version_id, _now(), _now()
            db.commit()
            return PromptModel.model_validate(_public(row))

    def toggle(self, prompt_id: str) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            if row is None:
                return None
            row.is_active = not (True if row.is_active is None else row.is_active)
            row.timestamp = row.updated_at = _now()
            db.commit()
            return PromptModel.model_validate(_public(row))

    def replace_grants(self, prompt_id: str, grants: list[dict[str, Any]]) -> Optional[PromptModel]:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            if row is None:
                return None
            now = _now()
            normalized = _normalize_grants(prompt_id, grants, now)
            row.access_grants, row.updated_at, row.timestamp = normalized, now, now
            db.commit()
            return PromptModel.model_validate(_public(row))

    def get_history(self, prompt_id: str, page: int = 0) -> list[PromptHistoryResponse]:
        with get_db() as db:
            rows = (db.query(PromptHistory).filter_by(prompt_id=prompt_id)
                    .order_by(PromptHistory.created_at.asc(), PromptHistory.id.asc())
                    .offset(max(page, 0) * 50).limit(50).all())
            return [PromptHistoryResponse.model_validate({
                **PromptHistoryModel.model_validate(row).model_dump(),
                "user": (
                    user.model_dump()
                    if (user := Users.get_user_by_id(row.user_id))
                    else None
                ),
            }) for row in rows]

    def get_history_entry(self, prompt_id: str, history_id: str) -> Optional[PromptHistoryModel]:
        with get_db() as db:
            row = db.query(PromptHistory).filter_by(id=history_id, prompt_id=prompt_id).first()
            return PromptHistoryModel.model_validate(row) if row else None

    def delete_history_entry(self, prompt_id: str, history_id: str) -> bool:
        with get_db() as db:
            count = db.query(PromptHistory).filter_by(id=history_id, prompt_id=prompt_id).delete()
            db.commit()
            return bool(count)

    def delete_prompt_by_command(self, command: str) -> bool:
        with get_db() as db:
            row = db.query(Prompt).filter_by(command=command).first()
            if row is None:
                return False
            if row.id:
                db.query(PromptHistory).filter_by(prompt_id=row.id).delete()
            db.delete(row)
            db.commit()
            return True

    def delete_prompt_by_id(self, prompt_id: str) -> bool:
        with get_db() as db:
            row = db.query(Prompt).filter_by(id=prompt_id).first()
            if row is None:
                return False
            db.query(PromptHistory).filter_by(prompt_id=prompt_id).delete()
            db.delete(row)
            db.commit()
            return True


Prompts = PromptsTable()
