# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Persistence and public forms for reusable Skills."""

import time
import uuid
from pathlib import PurePosixPath
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Boolean, Column, JSON, String, Text

from open_webui.internal.db import Base, get_db
from open_webui.models.users import UserResponse, Users


MAX_SKILL_FILES = 64
MAX_SKILL_BYTES = 1024 * 1024


class Skill(Base):
    __tablename__ = "skill"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=False)
    name = Column(Text, nullable=False)
    content = Column(Text, nullable=False, default="")
    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    version_id = Column(String, nullable=True)
    access_grants = Column(JSON, nullable=True)
    updated_at = Column(BigInteger, nullable=False)
    created_at = Column(BigInteger, nullable=False)


class AccessGrantModel(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_at: int


class SkillFile(BaseModel):
    path: str
    content: str
    encoding: Optional[Literal["base64"]] = None
    model_config = ConfigDict(extra="forbid")


class SkillFileOperation(BaseModel):
    op: Literal["put", "move", "delete"]
    path: str
    content: Optional[str] = None
    destination: Optional[str] = None
    encoding: Optional[Literal["base64"]] = None


class SkillData(BaseModel):
    files: list[SkillFile]
    model_config = ConfigDict(extra="forbid")


class SkillMeta(BaseModel):
    tags: Optional[list[str]] = Field(default_factory=list)
    i18n: Optional[dict[str, dict[str, str]]] = None


class SkillForm(BaseModel):
    id: str
    name: str
    content: Optional[str] = None
    data: Optional[SkillData] = None
    files: Optional[list[SkillFile]] = None
    operations: Optional[list[SkillFileOperation]] = None
    meta: SkillMeta = Field(default_factory=SkillMeta)
    description: Optional[str] = None
    access_grants: Optional[list[dict[str, Any]]] = None
    commit_message: Optional[str] = None
    expected_version_id: Optional[str] = None
    is_active: bool = True


class SkillAccessGrantsForm(BaseModel):
    access_grants: list[dict[str, Any]]


class SkillResponse(BaseModel):
    id: str
    user_id: str
    name: str
    meta: SkillMeta
    description: Optional[str] = None
    is_active: bool = True
    version_id: Optional[str] = None
    access_grants: list[AccessGrantModel] = Field(default_factory=list)
    updated_at: int
    created_at: int


class SkillModel(SkillResponse):
    content: str
    data: SkillData


class SkillUserResponse(SkillResponse):
    user: Optional[UserResponse] = None


class SkillAccessResponse(SkillResponse):
    user: Optional[UserResponse] = None
    write_access: Optional[bool] = False


class SkillDetailResponse(SkillAccessResponse):
    content: str


class SkillAccessListResponse(BaseModel):
    items: list[SkillAccessResponse] = Field(default_factory=list)
    total: int = 0


def _now() -> int:
    return int(time.time())


def _safe_path(raw: str) -> str:
    if not isinstance(raw, str) or not raw or "\x00" in raw or raw.startswith("/"):
        raise ValueError("invalid_skill_path")
    path = PurePosixPath(raw)
    if any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("invalid_skill_path")
    return path.as_posix()


def _validate_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(files) > MAX_SKILL_FILES:
        raise ValueError("skill_file_limit")
    result, seen, total = [], set(), 0
    for raw in files:
        value = (
            raw.model_dump(exclude_none=True)
            if hasattr(raw, "model_dump")
            else dict(raw)
        )
        value["path"] = _safe_path(value.get("path"))
        if value["path"] in seen:
            raise ValueError("duplicate_skill_path")
        seen.add(value["path"])
        content = value.get("content")
        if not isinstance(content, str):
            raise ValueError("invalid_skill_content")
        total += len(content.encode())
        if total > MAX_SKILL_BYTES:
            raise ValueError("skill_size_limit")
        result.append(value)
    return result


def _files_from_form(
    form: SkillForm, existing: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    if form.files is not None:
        files = [item.model_dump(exclude_none=True) for item in form.files]
    elif form.data is not None:
        files = [item.model_dump(exclude_none=True) for item in form.data.files]
    elif form.content is not None:
        files = [{"path": "SKILL.md", "content": form.content}]
    else:
        files = list((existing or {}).get("files") or [])
    by_path = {item["path"]: item for item in _validate_files(files)}
    for operation in form.operations or []:
        source = _safe_path(operation.path)
        if operation.op == "delete":
            by_path.pop(source, None)
        elif operation.op == "move":
            target = _safe_path(operation.destination)
            if source not in by_path or target in by_path:
                raise ValueError("invalid_skill_move")
            value = by_path.pop(source)
            value["path"] = target
            by_path[target] = value
        else:
            if operation.content is None:
                raise ValueError("invalid_skill_content")
            value = {"path": source, "content": operation.content}
            if operation.encoding:
                value["encoding"] = operation.encoding
            by_path[source] = value
    return _validate_files(list(by_path.values()))


def _normalize_grants(skill_id: str, grants, now: int) -> list[dict[str, Any]]:
    normalized, seen = [], set()
    for raw in grants or []:
        if not isinstance(raw, dict):
            raise ValueError("invalid_grant")
        principal_type, principal_id = raw.get("principal_type"), raw.get(
            "principal_id"
        )
        permission = raw.get("permission")
        if principal_type not in ("user", "group") or permission not in (
            "read",
            "write",
        ):
            raise ValueError("invalid_grant")
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("invalid_grant")
        key = (principal_type, principal_id, permission)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "id": str(uuid.uuid4()),
                "resource_type": "skill",
                "resource_id": skill_id,
                "principal_type": principal_type,
                "principal_id": principal_id,
                "permission": permission,
                "created_at": now,
            }
        )
    return normalized


def _public(row: Skill, *, detail: bool = False) -> dict[str, Any]:
    value = {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "meta": row.meta or {"tags": [], "i18n": None},
        "description": row.description,
        "is_active": row.is_active,
        "version_id": row.version_id,
        "access_grants": row.access_grants or [],
        "updated_at": row.updated_at,
        "created_at": row.created_at,
    }
    if detail:
        value.update({"content": row.content, "data": row.data or {"files": []}})
    return value


class SkillsTable:
    @staticmethod
    def has_grant(grants, user_id: str, permission: str) -> bool:
        from open_webui.models.groups import Groups

        group_ids = {group.id for group in Groups.get_groups_by_member_id(user_id)}
        for raw in grants or []:
            grant = raw.model_dump() if hasattr(raw, "model_dump") else raw
            if grant.get("permission") != permission:
                continue
            if grant.get("principal_type") == "user" and grant.get("principal_id") in (
                user_id,
                "*",
            ):
                return True
            if (
                grant.get("principal_type") == "group"
                and grant.get("principal_id") in group_ids
            ):
                return True
        return False

    def insert(self, user_id: str, form: SkillForm) -> Optional[SkillResponse]:
        now = _now()
        with get_db() as db:
            if db.query(Skill).filter_by(id=form.id).first():
                return None
            files = _files_from_form(form)
            content = next(
                (item["content"] for item in files if item["path"] == "SKILL.md"),
                form.content or "",
            )
            row = Skill(
                id=form.id,
                user_id=user_id,
                name=form.name,
                content=content,
                data={"files": files},
                meta=form.meta.model_dump(),
                description=form.description,
                is_active=form.is_active,
                version_id=str(uuid.uuid4()),
                access_grants=_normalize_grants(form.id, form.access_grants, now),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.commit()
            return SkillResponse.model_validate(_public(row))

    def get(self, skill_id: str) -> Optional[SkillModel]:
        with get_db() as db:
            row = db.query(Skill).filter_by(id=skill_id).first()
            return SkillModel.model_validate(_public(row, detail=True)) if row else None

    def list(self) -> list[SkillModel]:
        with get_db() as db:
            return [
                SkillModel.model_validate(_public(row, detail=True))
                for row in db.query(Skill)
                .order_by(Skill.updated_at.desc(), Skill.id.asc())
                .all()
            ]

    def update(self, skill_id: str, form: SkillForm) -> Optional[SkillModel]:
        with get_db() as db:
            row = db.query(Skill).filter_by(id=skill_id).first()
            if row is None:
                return None
            if form.expected_version_id and form.expected_version_id != row.version_id:
                raise RuntimeError("stale_skill_version")
            files = _files_from_form(form, row.data)
            row.name, row.description, row.meta = (
                form.name,
                form.description,
                form.meta.model_dump(),
            )
            row.data = {"files": files}
            row.content = next(
                (item["content"] for item in files if item["path"] == "SKILL.md"), ""
            )
            row.is_active, row.version_id, row.updated_at = (
                form.is_active,
                str(uuid.uuid4()),
                _now(),
            )
            db.commit()
            return SkillModel.model_validate(_public(row, detail=True))

    def toggle(self, skill_id: str) -> Optional[SkillModel]:
        with get_db() as db:
            row = db.query(Skill).filter_by(id=skill_id).first()
            if row is None:
                return None
            row.is_active, row.updated_at = not row.is_active, _now()
            db.commit()
            return SkillModel.model_validate(_public(row, detail=True))

    def replace_grants(self, skill_id: str, grants) -> Optional[SkillModel]:
        with get_db() as db:
            row = db.query(Skill).filter_by(id=skill_id).first()
            if row is None:
                return None
            row.access_grants, row.updated_at = (
                _normalize_grants(skill_id, grants, _now()),
                _now(),
            )
            db.commit()
            return SkillModel.model_validate(_public(row, detail=True))

    def delete(self, skill_id: str) -> bool:
        with get_db() as db:
            count = db.query(Skill).filter_by(id=skill_id).delete()
            db.commit()
            return bool(count)


Skills = SkillsTable()
