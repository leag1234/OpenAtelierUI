# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded collaborative comments for local resources."""

import hashlib
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from open_webui.models.chats import Chats
from open_webui.models.comments import Comments
from open_webui.models.knowledge import Knowledges
from open_webui.models.models import Models
from open_webui.models.prompts import Prompts
from open_webui.models.skills import Skills
from open_webui.models.tools import Tools
from open_webui.models.users import Users
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_verified_user


router = APIRouter()
ResourceType = Literal["model", "knowledge", "prompt", "skill", "tool", "note", "chat"]
TargetType = Literal["note_page", "chat_message"]


class CommentAnchor(BaseModel):
    quote: str = Field(min_length=1, max_length=10_000)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    prefix: str = Field(default="", max_length=64)
    suffix: str = Field(default="", max_length=64)
    surface_id: str = Field(max_length=256)
    source: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config = ConfigDict(extra="forbid")


class CommentForm(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    resource_type: ResourceType
    resource_id: str = Field(min_length=1, max_length=512)
    target_type: Optional[TargetType] = None
    target_id: Optional[str] = Field(default=None, max_length=256)
    anchor: Optional[CommentAnchor] = None
    temp_id: Optional[UUID] = None
    model_config = ConfigDict(extra="forbid")


class CommentReplyForm(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    reply_to_id: Optional[str] = None
    temp_id: Optional[UUID] = None
    model_config = ConfigDict(extra="forbid")


class CommentUpdateForm(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    etag: str = Field(pattern=r"^\d{1,20}$")
    model_config = ConfigDict(extra="forbid")


class ReactionForm(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _missing_comment():
    return HTTPException(status_code=404, detail="Comment not found")


def _missing_resource():
    return HTTPException(status_code=404, detail="Resource not found")


def _enabled(request):
    if not request.app.state.config.ENABLE_COMMENTS:
        raise HTTPException(status_code=403, detail="Comments are disabled")


def _note(resource_id):
    try:
        from open_webui.routers.notes import notes_store
        return notes_store.get(resource_id) if notes_store is not None else None
    except Exception:
        return None


def _resource_object(resource_type, resource_id):
    if resource_type == "chat":
        return Chats.get_chat_by_id(resource_id)
    if resource_type == "model":
        return Models.get_model_by_id(resource_id)
    if resource_type == "knowledge":
        return Knowledges.get_knowledge_by_id(resource_id)
    if resource_type == "prompt":
        return Prompts.get_prompt_by_id(resource_id)
    if resource_type == "skill":
        return Skills.get(resource_id)
    if resource_type == "tool":
        return Tools.get_tool_by_id(resource_id)
    if resource_type == "note":
        return _note(resource_id)
    return None


def _value(resource, key, default=None):
    return resource.get(key, default) if isinstance(resource, dict) else getattr(resource, key, default)


def _can(resource_type, resource, user, permission="read"):
    if user.role == "admin" or _value(resource, "user_id") == user.id:
        return True
    if resource_type in ("model", "knowledge", "tool"):
        return has_access(user.id, permission, _value(resource, "access_control"))
    if resource_type == "prompt":
        return Prompts.has_grant(_value(resource, "access_grants"), user.id, permission)
    if resource_type == "skill":
        return Skills.has_grant(_value(resource, "access_grants"), user.id, permission)
    if resource_type == "note":
        try:
            from open_webui.models.groups import Groups
            from open_webui.routers.notes import notes_store
            groups = {group.id for group in Groups.get_groups_by_member_id(user.id)}
            return bool(notes_store and notes_store.has_grant(_value(resource, "id"), user.id, permission, groups))
        except Exception:
            return False
    return False


def _resource_for(resource_type, resource_id, user, permission="read"):
    resource = _resource_object(resource_type, resource_id)
    if resource is None or not _can(resource_type, resource, user, permission):
        raise _missing_resource()
    return resource


def _resource_title(resource_type, resource):
    if resource_type == "chat":
        return _value(resource, "title")
    if resource_type == "prompt":
        return _value(resource, "name", _value(resource, "title"))
    return _value(resource, "name", _value(resource, "title", ""))


def _reaction_projection(comment_id, user_id):
    grouped = {}
    for reaction in Comments.reactions(comment_id):
        item = grouped.setdefault(
            reaction["name"], {"name": reaction["name"], "count": 0, "reacted": False}
        )
        item["count"] += 1
        if reaction["user_id"] == user_id:
            item["reacted"] = True
    return list(grouped.values())


def _response(comment, user):
    deleted = comment.deleted_at is not None
    author = Users.get_user_by_id(comment.user_id) if comment.user_id else None
    count, latest = Comments.reply_summary(comment.id)
    return {
        "id": comment.id,
        "user_id": None if deleted else comment.user_id,
        "resource_type": comment.resource_type,
        "resource_id": comment.resource_id,
        "parent_id": comment.parent_id,
        "reply_to_id": comment.reply_to_id,
        "content": "" if deleted else comment.content,
        "target_type": comment.target_type,
        "target_id": comment.target_id,
        "meta": comment.meta,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "deleted_at": comment.deleted_at,
        "etag": str(comment.updated_at),
        "user": None if deleted or author is None else {
            "id": author.id,
            "name": author.name,
            "profile_image_url": author.profile_image_url,
        },
        "can_edit": not deleted and comment.user_id == user.id,
        "can_delete": not deleted and (comment.user_id == user.id or user.role == "admin"),
        "latest_reply_at": latest,
        "reply_count": count,
        "reactions": _reaction_projection(comment.id, user.id),
    }


def _comment_for(comment_id, user):
    comment = Comments.get(comment_id)
    if comment is None:
        raise _missing_comment()
    _resource_for(comment.resource_type, comment.resource_id, user)
    return comment


def _validate_anchor(form, resource):
    if form.anchor is None:
        return
    if form.target_type != "chat_message" or not form.target_id:
        raise HTTPException(status_code=409, detail="Message output changed. Select the passage again.")
    chat = _value(resource, "chat", {})
    message = chat.get("history", {}).get("messages", {}).get(form.target_id, {}) if isinstance(chat, dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise HTTPException(status_code=409, detail="Message output changed. Select the passage again.")
    source = hashlib.sha256(form.target_id.encode()).hexdigest()
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    anchor = form.anchor
    if (
        anchor.source != source
        or anchor.content_hash != content_hash
        or content[anchor.start:anchor.end] != anchor.quote
    ):
        raise HTTPException(status_code=409, detail="Message output changed. Select the passage again.")


@router.get("/")
async def list_resource_comments(
    request: Request,
    resource_type: ResourceType,
    resource_id: str,
    cursor: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=50),
    anchored: bool = False,
    user=Depends(get_verified_user),
):
    _enabled(request)
    _resource_for(resource_type, resource_id, user)
    rows = Comments.list_roots(resource_type, resource_id, cursor, limit, anchored)
    if rows[0] is None:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    items, total, next_cursor = rows
    return {"items": [_response(item, user) for item in items], "next_cursor": next_cursor, "total": total}


@router.post("/")
async def create_comment(request: Request, form_data: CommentForm, user=Depends(get_verified_user)):
    _enabled(request)
    resource = _resource_for(form_data.resource_type, form_data.resource_id, user, "write")
    _validate_anchor(form_data, resource)
    comment = Comments.create(user.id, form_data)
    if comment is None:
        raise HTTPException(status_code=409, detail="Comment already exists")
    return _response(comment, user)


@router.get("/context")
async def context(request: Request, resource_type: ResourceType, resource_id: str, user=Depends(get_verified_user)):
    _enabled(request)
    resource = _resource_for(resource_type, resource_id, user)
    return {"resource_type": resource_type, "resource_id": resource_id, "title": _resource_title(resource_type, resource)}


@router.get("/mentions")
async def mentions(
    request: Request,
    resource_type: ResourceType,
    resource_id: str,
    query: str = Query(default="", max_length=100),
    user=Depends(get_verified_user),
):
    _enabled(request)
    resource = _resource_for(resource_type, resource_id, user)
    needle = query.casefold()
    result = []
    for candidate in Users.get_users():
        if candidate.role not in ("admin", "user") or not _can(resource_type, resource, candidate):
            continue
        if needle and needle not in candidate.name.casefold():
            continue
        result.append({"id": candidate.id, "name": candidate.name})
    return sorted(result, key=lambda item: (item["name"].casefold(), item["id"]))


@router.get("/{id}")
async def get_comment(request: Request, id: str, user=Depends(get_verified_user)):
    comment = _comment_for(id, user)
    _enabled(request)
    return _response(comment, user)


@router.patch("/{id}")
async def edit(request: Request, id: str, form_data: CommentUpdateForm, user=Depends(get_verified_user)):
    comment = _comment_for(id, user)
    _enabled(request)
    if comment.deleted_at is not None or comment.user_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot edit this comment")
    updated, error = Comments.update(id, form_data.content, form_data.etag)
    if error == "conflict":
        raise HTTPException(status_code=409, detail="Comment changed. Reload before editing.")
    if updated is None:
        raise _missing_comment()
    return _response(updated, user)


@router.delete("/{id}")
async def remove(
    request: Request,
    id: str,
    etag: str = Query(pattern=r"^\d{1,20}$"),
    user=Depends(get_verified_user),
):
    comment = _comment_for(id, user)
    _enabled(request)
    if comment.deleted_at is not None or (comment.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=403, detail="Cannot delete this comment")
    _, error = Comments.soft_delete(id, etag)
    if error == "conflict":
        raise HTTPException(status_code=409, detail="Comment changed. Reload before deleting.")
    if error == "missing":
        raise _missing_comment()
    return True


@router.get("/{id}/replies")
async def replies(
    request: Request,
    id: str,
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=100),
    user=Depends(get_verified_user),
):
    _comment_for(id, user)
    _enabled(request)
    items, next_cursor = Comments.replies(id, cursor, limit)
    if items is None:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return {"items": [_response(item, user) for item in items], "next_cursor": next_cursor}


@router.post("/{id}/replies")
async def reply(request: Request, id: str, form_data: CommentReplyForm, user=Depends(get_verified_user)):
    parent = _comment_for(id, user)
    _enabled(request)
    if parent.deleted_at is not None:
        raise _missing_comment()
    if form_data.reply_to_id:
        target = Comments.get(form_data.reply_to_id)
        if target is None or target.parent_id != id:
            raise _missing_comment()
    comment = Comments.create(user.id, form_data, parent=parent)
    if comment is None:
        raise HTTPException(status_code=409, detail="Comment already exists")
    return _response(comment, user)


@router.put("/{id}/reactions")
async def add_reaction(request: Request, id: str, form_data: ReactionForm, user=Depends(get_verified_user)):
    comment = _comment_for(id, user)
    _enabled(request)
    if comment.deleted_at is not None:
        raise _missing_comment()
    return Comments.add_reaction(id, user.id, form_data.name)


@router.delete("/{id}/reactions")
async def remove_reaction(
    request: Request,
    id: str,
    name: str = Query(min_length=1, max_length=100),
    user=Depends(get_verified_user),
):
    comment = _comment_for(id, user)
    _enabled(request)
    if comment.deleted_at is not None:
        raise _missing_comment()
    return Comments.remove_reaction(id, user.id, name)
