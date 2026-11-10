# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured compatibility routes for reusable Skills."""

import asyncio
import difflib
import ipaddress
import json
import os
import re
import socket
from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlparse

import aiohttp
import yaml
from pydantic import BaseModel
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    status,
)
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
from open_webui.utils.bounded_url_loader import LoadUrlForm
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
MAX_SKILL_IMPORT_BYTES = 16 * 1024 * 1024
MAX_SKILL_IMPORT_FILES = 64


class CloneSkillForm(BaseModel):
    id: str
    name: str
    version_id: Optional[str] = None


class SkillVersionUpdateForm(BaseModel):
    version_id: str
    expected_version_id: str


def _skill_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    if not slug:
        raise HTTPException(status_code=400, detail="Skill name is required")
    return slug[:128]


def _frontmatter(content: str) -> tuple[dict, str]:
    if not content.startswith("---\n"):
        raise HTTPException(status_code=400, detail="SKILL.md frontmatter is required")
    marker = content.find("\n---\n", 4)
    if marker < 0:
        raise HTTPException(status_code=400, detail="SKILL.md frontmatter is invalid")
    try:
        value = yaml.safe_load(content[4:marker]) or {}
    except yaml.YAMLError as error:
        raise HTTPException(status_code=400, detail="SKILL.md frontmatter is invalid") from error
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="SKILL.md frontmatter is invalid")
    return value, content[marker + 5 :]


def _markdown_package(filename: str, raw: bytes) -> dict:
    suffix = PurePosixPath(filename).suffix.casefold()
    if suffix != ".md":
        raise HTTPException(status_code=400, detail="Use JSON, ZIP, .skill, or Markdown")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=400, detail="Skill Markdown must be UTF-8") from error
    metadata, _body = _frontmatter(content)
    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise HTTPException(status_code=400, detail="Skill name is required")
    if description is not None and not isinstance(description, str):
        raise HTTPException(status_code=400, detail="Skill description is invalid")
    return {
        "id": _skill_slug(name),
        "name": name.strip(),
        "description": description,
        "meta": {},
        "files": [{"path": "SKILL.md", "content": content}],
    }


async def _uploaded_packages(request: Request, files: list[bytes]) -> list[dict]:
    if len(files) > MAX_SKILL_IMPORT_FILES:
        raise HTTPException(status_code=413, detail="Too many skill files")
    form = await request.form()
    uploads = form.getlist("files")
    packages, total = [], 0
    for index, raw in enumerate(files):
        total += len(raw)
        if total > MAX_SKILL_IMPORT_BYTES:
            raise HTTPException(status_code=413, detail="Skill import exceeds the size limit")
        filename = getattr(uploads[index], "filename", "") if index < len(uploads) else ""
        packages.append(_markdown_package(filename or "", raw))
    return packages


def _import_allowed(request: Request, user) -> bool:
    return user.role == "admin" or has_permission(
        user.id, "workspace.skills_import", request.app.state.config.USER_PERMISSIONS
    )


def _preview(packages: list[dict], user) -> list[dict]:
    existing = Skills.list()
    by_id = {item.id: item for item in existing}
    names = {item.name: item for item in existing}
    result = []
    for index, package in enumerate(packages):
        current = by_id.get(package["id"]) or names.get(package["name"])
        result.append(
            {
                "index": index,
                "id": package["id"],
                "name": package["name"],
                "description": package["description"],
                "meta": package["meta"],
                "files": [
                    {
                        "path": item["path"],
                        "size": len(item["content"].encode("utf-8")),
                        "encoding": None,
                    }
                    for item in package["files"]
                ],
                "id_taken": package["id"] in by_id,
                "name_taken": package["name"] in names,
                "can_replace": current is not None
                and (user.role == "admin" or current.user_id == user.id),
                "expected_version_id": current.version_id if current is not None else None,
            }
        )
    return result


def _allow_local_skill_fetch() -> bool:
    return any(
        os.environ.get(name, "False").casefold() == "true"
        for name in ("ENABLE_LOCAL_WEB_FETCH", "ENABLE_RAG_LOCAL_WEB_FETCH")
    )


async def _validate_skill_url(value: str):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid skill URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise HTTPException(status_code=400, detail="Invalid skill URL")
    if _allow_local_skill_fetch():
        return
    try:
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            ),
        )
        addresses = {record[4][0] for record in records}
    except OSError as error:
        raise HTTPException(status_code=400, detail="Skill URL could not be resolved") from error
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise HTTPException(status_code=400, detail="Private skill URLs are disabled")


async def _load_skill_url(value: str) -> list[dict]:
    await _validate_skill_url(value)
    parsed = urlparse(value)
    filename = PurePosixPath(parsed.path).name
    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.get(value, allow_redirects=False) as response:
                if response.status != 200:
                    raise HTTPException(status_code=400, detail="Skill URL could not be loaded")
                raw = await response.content.read(MAX_SKILL_IMPORT_BYTES + 1)
    except HTTPException:
        raise
    except (asyncio.TimeoutError, aiohttp.ClientError) as error:
        raise HTTPException(status_code=400, detail="Skill URL could not be loaded") from error
    if len(raw) > MAX_SKILL_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Skill import exceeds the size limit")
    return [_markdown_package(filename, raw)]


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


@router.post("/import/preview")
async def preview_skill_import(
    request: Request,
    files: list[bytes] = File(...),
    user=Depends(get_verified_user),
):
    if not _import_allowed(request, user):
        raise HTTPException(status_code=403, detail="Import permission required")
    return _preview(await _uploaded_packages(request, files), user)


@router.post("/import")
async def import_skills(
    request: Request,
    files: list[bytes] = File(...),
    decisions: str = Form(...),
    user=Depends(get_verified_user),
):
    if not _import_allowed(request, user):
        raise HTTPException(status_code=403, detail="Import permission required")
    packages = await _uploaded_packages(request, files)
    try:
        choices = json.loads(decisions)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Import decisions are invalid") from error
    if not isinstance(choices, list) or len(choices) != len(packages):
        raise HTTPException(status_code=400, detail="One decision is required per skill")
    results = []
    for index, (package, choice) in enumerate(zip(packages, choices)):
        if not isinstance(choice, dict) or choice.get("index", index) != index:
            raise HTTPException(status_code=400, detail="Import decisions are invalid")
        action = choice.get("action")
        if action == "skip":
            results.append({"id": package["id"], "status": "skipped"})
            continue
        skill_id = choice.get("id") or package["id"]
        name = choice.get("name") or package["name"]
        form = SkillForm.model_validate(
            {
                "id": skill_id,
                "name": name,
                "description": package["description"],
                "meta": package["meta"],
                "files": package["files"],
                "commit_message": "Imported skill",
            }
        )
        if action in {"create", "copy"}:
            inserted = Skills.insert(user.id, form)
            if inserted is None:
                raise HTTPException(status_code=400, detail="Skill already exists")
            skill = Skills.get(skill_id)
            if skill is None:
                raise HTTPException(status_code=500, detail="Skill import failed")
            _record(skill, user.id, "Imported skill")
        elif action == "replace":
            current = _get(skill_id)
            _require(current, user, "write")
            expected = choice.get("expected_version_id")
            if expected is not None and expected != current.version_id:
                raise HTTPException(status_code=409, detail="Skill version changed")
            form.expected_version_id = expected
            form.access_grants = [item.model_dump() for item in current.access_grants]
            updated = Skills.update(skill_id, form)
            if updated is None:
                raise _not_found()
            _record(updated, user.id, "Imported skill")
        else:
            raise HTTPException(status_code=400, detail="Import decision is invalid")
        results.append({"id": skill_id, "status": "saved"})
    return results


@router.post("/load/url")
async def load_skill_by_url(
    form_data: LoadUrlForm, user=Depends(get_verified_user)
):
    if user.role != "admin":
        raise HTTPException(
            status_code=401, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
        )
    return await _load_skill_url(str(form_data.url))


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
