# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured compatibility routes for reusable Skills."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.skills import (
    SkillAccessGrantsForm,
    SkillAccessListResponse,
    SkillAccessResponse,
    SkillDetailResponse,
    SkillForm,
    SkillModel,
    SkillResponse,
    SkillUserResponse,
    Skills,
)
from open_webui.models.users import Users
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user


router = APIRouter()


def _not_found():
    return HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)


def _denied():
    return HTTPException(status_code=401, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def _get(skill_id: str):
    skill = Skills.get(skill_id)
    if skill is None:
        raise _not_found()
    return skill


def _can(skill, user, permission: str) -> bool:
    if user.role == "admin" or skill.user_id == user.id:
        return True
    return Skills.has_grant(skill.access_grants, user.id, permission)


def _require(skill, user, permission: str):
    if not _can(skill, user, permission):
        raise _denied()


def _visible(user, permission="read"):
    return [
        skill
        for skill in Skills.list()
        if _can(skill, user, permission)
        or (permission == "read" and _can(skill, user, "write"))
    ]


def _response(skill, user, *, detail=False):
    owner = Users.get_user_by_id(skill.user_id)
    response_type = SkillDetailResponse if detail else SkillAccessResponse
    return response_type.model_validate(
        {
            **skill.model_dump(),
            "user": owner.model_dump() if owner else None,
            "write_access": _can(skill, user, "write"),
        }
    )


def _catalog_response(skill):
    owner = Users.get_user_by_id(skill.user_id)
    return SkillUserResponse.model_validate(
        {
            **skill.model_dump(),
            "user": owner.model_dump() if owner else None,
        }
    )


@router.get("/", response_model=list[SkillUserResponse])
async def get_skills(query: Optional[str] = None, user=Depends(get_verified_user)):
    values = _visible(user)
    if query:
        needle = query.casefold()
        values = [
            item
            for item in values
            if needle in item.name.casefold() or needle in item.id.casefold()
        ]
    return [_catalog_response(item) for item in values]


@router.get("/list", response_model=SkillAccessListResponse)
async def get_skill_list(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
):
    _ = (view_option, order_by, direction)
    values = _visible(user)
    if query:
        needle = query.casefold()
        values = [
            item
            for item in values
            if needle in item.name.casefold() or needle in item.id.casefold()
        ]
    total = len(values)
    offset = max((page or 1) - 1, 0) * 50
    return SkillAccessListResponse(
        items=[_response(item, user) for item in values[offset : offset + 50]],
        total=total,
    )


@router.get("/export")
async def export_skills(request: Request, user=Depends(get_verified_user)):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.skills_export", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(status_code=403, detail="Export permission required")
    return [
        {
            "id": item.id,
            "name": item.name,
            "description": item.description,
            "meta": item.meta.model_dump(),
            "is_active": item.is_active,
            "files": [entry.model_dump(exclude_none=True) for entry in item.data.files],
        }
        for item in _visible(user)
    ]


@router.post("/create", response_model=SkillResponse)
async def create_new_skill(
    request: Request, form_data: SkillForm, user=Depends(get_verified_user)
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.skills", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.UNAUTHORIZED
        )
    try:
        result = Skills.insert(user.id, form_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT())
    return result


@router.get("/id/{id}", response_model=SkillDetailResponse)
async def get_skill_by_id(id: str, user=Depends(get_verified_user)):
    skill = _get(id)
    _require(skill, user, "read")
    return _response(skill, user, detail=True)


@router.post("/id/{id}/update", response_model=SkillModel)
async def update_skill_by_id(
    id: str, form_data: SkillForm, user=Depends(get_verified_user)
):
    skill = _get(id)
    _require(skill, user, "write")
    if form_data.id != id:
        raise HTTPException(status_code=422, detail="Skill id does not match path")
    try:
        result = Skills.update(id, form_data)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Skill version changed") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if result is None:
        raise _not_found()
    return result


@router.post("/id/{id}/toggle", response_model=SkillModel)
async def toggle_skill_by_id(id: str, user=Depends(get_verified_user)):
    skill = _get(id)
    _require(skill, user, "write")
    result = Skills.toggle(id)
    if result is None:
        raise _not_found()
    return result


@router.post("/id/{id}/access/update", response_model=SkillModel)
async def update_skill_access_by_id(
    id: str, form_data: SkillAccessGrantsForm, user=Depends(get_verified_user)
):
    skill = _get(id)
    if user.role != "admin" and skill.user_id != user.id:
        raise _denied()
    try:
        result = Skills.replace_grants(id, form_data.access_grants)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid access grant") from None
    if result is None:
        raise _not_found()
    return result


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_skill_by_id(id: str, user=Depends(get_verified_user)):
    skill = _get(id)
    if user.role != "admin" and skill.user_id != user.id:
        raise _denied()
    return Skills.delete(id)
