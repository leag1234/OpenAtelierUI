# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured compatibility routes for reusable Skills."""

import difflib
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

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
from open_webui.env import DATA_DIR
from open_webui.utils.version_history import (
    VersionHistoryNotFound,
    VersionHistoryUnavailable,
    append_version,
    current_version_id,
    delete_version,
    ensure_root,
    get_version,
    list_versions,
    select_version,
)


router = APIRouter()


class CloneSkillForm(BaseModel):
    id: str
    name: str
    version_id: Optional[str] = None


class SkillVersionUpdateForm(BaseModel):
    version_id: str
    expected_version_id: str


def _snapshot(skill) -> dict:
    return {
        "name": skill.name,
        "content": skill.content,
        "data": skill.data.model_dump(),
        "meta": skill.meta.model_dump(),
        "description": skill.description,
        "is_active": skill.is_active,
    }


def _root(skill, user_id: str) -> str:
    version_id = ensure_root(
        DATA_DIR, "skill", skill.id, skill.user_id, _snapshot(skill), user_id
    )
    if skill.version_id != version_id:
        updated = Skills.set_version_id(skill.id, version_id)
        if updated is not None:
            skill = updated
    return version_id


def _record(skill, user_id: str, message: Optional[str]) -> SkillModel:
    version_id = append_version(
        DATA_DIR,
        "skill",
        skill.id,
        skill.user_id,
        _snapshot(skill),
        user_id,
        message,
    )
    updated = Skills.set_version_id(skill.id, version_id)
    if updated is None:
        raise VersionHistoryUnavailable("Version history resource disappeared")
    return updated


def _owner(skill, user):
    if user.role != "admin" and skill.user_id != user.id:
        raise _denied()


def _history_error(error: Exception):
    if isinstance(error, VersionHistoryNotFound):
        return HTTPException(status_code=404, detail="Version not found")
    return HTTPException(status_code=503, detail=str(error))


def _files(snapshot: dict) -> list[dict]:
    values = snapshot.get("data", {}).get("files", [])
    return values if isinstance(values, list) else []


def _file_listing(snapshot: dict, version_id: str) -> dict:
    files = []
    for item in _files(snapshot):
        content = item.get("content", "")
        if not isinstance(content, str):
            raise VersionHistoryUnavailable("Version snapshot is invalid")
        files.append(
            {
                "path": item.get("path"),
                "size": len(content.encode("utf-8")),
                "encoding": item.get("encoding"),
            }
        )
    return {"files": files, "version_id": version_id}


def _public_history(entry: dict) -> dict:
    result = {key: value for key, value in entry.items() if key != "snapshot"}
    result.pop("user", None)
    snapshot = entry.get("snapshot", {})
    result["snapshot"] = {
        key: value for key, value in snapshot.items() if key != "is_active"
    }
    result["snapshot"]["data"] = {
        "files": _file_listing(snapshot, entry["id"])["files"]
    }
    return result


def _skill_response(skill: SkillModel) -> JSONResponse:
    value = skill.model_dump(mode="json")
    for item in value.get("data", {}).get("files", []):
        if item.get("encoding") is None:
            item.pop("encoding", None)
    return JSONResponse(content=value)


def _not_found():
    return HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)


def _denied():
    return HTTPException(status_code=403, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


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
    skill = Skills.get(result.id)
    try:
        return _record(skill, user.id, form_data.commit_message) if skill else result
    except VersionHistoryUnavailable as error:
        Skills.delete(result.id)
        raise HTTPException(status_code=503, detail=str(error)) from None


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
        _root(skill, user.id)
        result = Skills.update(id, form_data)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Skill version changed") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if result is None:
        raise _not_found()
    try:
        return _record(result, user.id, form_data.commit_message)
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@router.get("/id/{id}/files")
async def get_skill_files(
    id: str, version_id: Optional[str] = None, user=Depends(get_verified_user)
):
    skill = _get(id)
    _require(skill, user, "read")
    if version_id is None:
        try:
            version_id = _root(skill, user.id)
            return _file_listing(_snapshot(skill), version_id)
        except VersionHistoryUnavailable as error:
            raise _history_error(error) from None
    try:
        entry = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, version_id, Users.get_user_by_id
        )
        return _file_listing(entry["snapshot"], version_id)
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.get("/id/{id}/files/content")
async def get_skill_file(
    id: str,
    path: str,
    version_id: str,
    user=Depends(get_verified_user),
):
    skill = _get(id)
    _require(skill, user, "read")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=422, detail="invalid_skill_path")
    try:
        entry = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, version_id, Users.get_user_by_id
        )
        for item in entry["snapshot"].get("data", {}).get("files", []):
            if item.get("path") == path:
                content = item.get("content")
                if not isinstance(content, str):
                    raise VersionHistoryUnavailable("Version snapshot is invalid")
                return Response(content=content, media_type="text/markdown")
        raise VersionHistoryNotFound()
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.get("/id/{id}/history")
async def get_skill_history(id: str, page: int = 1, user=Depends(get_verified_user)):
    skill = _get(id)
    _require(skill, user, "read")
    try:
        _root(skill, user.id)
        return list_versions(
            DATA_DIR, "skill", skill.id, skill.user_id, page, Users.get_user_by_id
        )
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.get("/id/{id}/history/diff")
async def diff_skill_history(
    id: str, from_id: str, to_id: str, user=Depends(get_verified_user)
):
    skill = _get(id)
    _require(skill, user, "read")
    try:
        before = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, from_id, Users.get_user_by_id
        )["snapshot"]
        after = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, to_id, Users.get_user_by_id
        )["snapshot"]
        before_files = {item.get("path"): item for item in _files(before)}
        after_files = {item.get("path"): item for item in _files(after)}
        files = []
        for path in sorted(before_files.keys() | after_files.keys()):
            if path not in before_files:
                state = "added"
            elif path not in after_files:
                state = "deleted"
            elif before_files[path] != after_files[path]:
                state = "modified"
            else:
                continue
            content = (before_files.get(path) or after_files.get(path) or {}).get(
                "content", ""
            )
            files.append(
                {"path": path, "status": state, "binary": not isinstance(content, str)}
            )
        metadata = {}
        for key in ("name", "description", "meta"):
            if before.get(key) != after.get(key):
                metadata[key] = {"before": before.get(key), "after": after.get(key)}
        return {"files": files, "metadata": metadata}
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.get("/id/{id}/history/diff/file")
async def diff_skill_file(
    id: str,
    from_id: str,
    to_id: str,
    path: str,
    user=Depends(get_verified_user),
):
    skill = _get(id)
    _require(skill, user, "read")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=422, detail="invalid_skill_path")
    try:
        before = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, from_id, Users.get_user_by_id
        )["snapshot"]
        after = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, to_id, Users.get_user_by_id
        )["snapshot"]

        def content(snapshot):
            for item in _files(snapshot):
                if item.get("path") == path and isinstance(item.get("content"), str):
                    return item["content"]
            raise VersionHistoryNotFound()

        old, new = content(before), content(after)
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(),
                new.splitlines(),
                fromfile=f"{from_id[:7]}/{path}",
                tofile=f"{to_id[:7]}/{path}",
                lineterm="",
            )
        )
        return {
            "binary": False,
            "diff": diff,
            "line_endings_only": old != new
            and old.replace("\r\n", "\n") == new.replace("\r\n", "\n"),
        }
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.get("/id/{id}/history/{history_id}")
async def get_skill_history_entry(
    id: str, history_id: str, user=Depends(get_verified_user)
):
    skill = _get(id)
    _require(skill, user, "read")
    try:
        entry = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, history_id,
            Users.get_user_by_id,
        )
        return _public_history(entry)
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


@router.delete("/id/{id}/history/{history_id}", response_model=bool)
async def delete_skill_history_entry(
    id: str, history_id: str, user=Depends(get_verified_user)
):
    skill = _get(id)
    _owner(skill, user)
    try:
        return delete_version(DATA_DIR, "skill", skill.id, skill.user_id, history_id)
    except VersionHistoryNotFound as error:
        raise _history_error(error) from None
    except VersionHistoryUnavailable as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.post("/id/{id}/clone")
async def clone_skill(
    id: str, form_data: CloneSkillForm, user=Depends(get_verified_user)
):
    source = _get(id)
    _require(source, user, "read")
    try:
        source_version = form_data.version_id or _root(source, user.id)
        entry = get_version(
            DATA_DIR, "skill", source.id, source.user_id, source_version,
            Users.get_user_by_id,
        )
        form = SkillForm.model_validate(
            {"id": form_data.id, "name": form_data.name, **entry["snapshot"]}
        )
        form.id, form.name, form.access_grants = form_data.id, form_data.name, []
        result = Skills.insert(user.id, form)
        if result is None:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT())
        clone = Skills.get(result.id)
        return (
            _skill_response(_record(clone, user.id, "Cloned skill"))
            if clone
            else result
        )
    except HTTPException:
        raise
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None


@router.post("/id/{id}/update/version", response_model=Optional[SkillModel])
async def set_skill_version(
    id: str,
    form_data: SkillVersionUpdateForm,
    user=Depends(get_verified_user),
):
    skill = _get(id)
    _owner(skill, user)
    if skill.version_id != form_data.expected_version_id:
        raise HTTPException(status_code=409, detail="Skill version changed")
    try:
        entry = get_version(
            DATA_DIR, "skill", skill.id, skill.user_id, form_data.version_id,
            Users.get_user_by_id,
        )
        updated = Skills.select_snapshot(
            id, entry["snapshot"], form_data.expected_version_id
        )
        if updated is None:
            raise VersionHistoryNotFound()
        select_version(DATA_DIR, "skill", skill.id, skill.user_id, form_data.version_id)
        selected = Skills.set_version_id(id, form_data.version_id)
        if selected is None:
            raise VersionHistoryNotFound()
        return _skill_response(selected)
    except RuntimeError:
        raise HTTPException(status_code=409, detail="Skill version changed") from None
    except (VersionHistoryNotFound, VersionHistoryUnavailable) as error:
        raise _history_error(error) from None


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
