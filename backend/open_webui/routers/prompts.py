# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Prompt API with measured stable-ID compatibility and legacy command routes."""

import difflib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.prompts import (
    PromptAccessGrantsForm,
    PromptAccessResponse,
    PromptForm,
    PromptHistoryModel,
    PromptHistoryResponse,
    PromptMetadataForm,
    PromptModel,
    PromptResponse,
    PromptUserResponse,
    PromptVersionUpdateForm,
    Prompts,
)
from open_webui.models.users import Users
from open_webui.utils.access_control import has_permission
from open_webui.utils.auth import get_verified_user


router = APIRouter()


def _not_found():
    return HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)


def _denied():
    return HTTPException(status_code=401, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def _get(prompt_id: str):
    prompt = Prompts.get_prompt_by_id(prompt_id)
    if prompt is None:
        raise _not_found()
    return prompt


def _can(prompt, user, permission: str) -> bool:
    return (
        user.role == "admin"
        or prompt.user_id == user.id
        or Prompts.has_grant(prompt.access_grants, user.id, permission)
    )


def _require(prompt, user, permission: str, conceal: bool = False):
    if not _can(prompt, user, permission):
        raise _not_found() if conceal else _denied()


@router.get("/", response_model=list[PromptResponse])
async def get_prompts(user=Depends(get_verified_user)):
    return Prompts.get_prompts() if user.role == "admin" else Prompts.get_prompts_by_user_id(user.id, "read")


@router.get("/list", response_model=list[PromptUserResponse])
async def get_prompt_list(user=Depends(get_verified_user)):
    return Prompts.get_prompts() if user.role == "admin" else Prompts.get_prompts_by_user_id(user.id, "write")


@router.get("/tags", response_model=list[str])
async def get_prompt_tags(user=Depends(get_verified_user)):
    prompts = Prompts.get_prompts() if user.role == "admin" else Prompts.get_prompts_by_user_id(user.id, "read")
    return sorted({tag for prompt in prompts for tag in prompt.tags})


@router.post("/create", response_model=Optional[PromptModel])
async def create_new_prompt(request: Request, form_data: PromptForm, user=Depends(get_verified_user)):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.prompts", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.UNAUTHORIZED)
    if Prompts.get_prompt_by_command(form_data.command) is not None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.COMMAND_TAKEN)
    prompt = Prompts.insert_new_prompt(user.id, form_data)
    if prompt is None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT())
    return prompt


@router.get("/id/{prompt_id}", response_model=Optional[PromptAccessResponse])
async def get_prompt_by_id(prompt_id: str, user=Depends(get_verified_user)):
    prompt = _get(prompt_id)
    _require(prompt, user, "read", conceal=True)
    owner = Users.get_user_by_id(prompt.user_id)
    return PromptAccessResponse.model_validate({
        **prompt.model_dump(), "user": owner.model_dump() if owner else None,
        "write_access": _can(prompt, user, "write"),
    })


@router.post("/id/{prompt_id}/update", response_model=Optional[PromptModel])
async def update_prompt_by_id(prompt_id: str, form_data: PromptForm, user=Depends(get_verified_user)):
    """Update a prompt's content, creating a new history entry if changed."""
    prompt = _get(prompt_id)
    _require(prompt, user, "write")
    try:
        result = Prompts.update_prompt_by_id(prompt_id, form_data, user.id)
    except ValueError:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.COMMAND_TAKEN) from None
    if result is None:
        raise _not_found()
    return result


@router.post("/id/{prompt_id}/update/meta", response_model=Optional[PromptModel])
async def update_prompt_metadata(
    prompt_id: str, form_data: PromptMetadataForm, user=Depends(get_verified_user)
):
    """Update prompt name and command only (no history created)."""
    prompt = _get(prompt_id)
    _require(prompt, user, "write")
    try:
        result = Prompts.update_metadata(prompt_id, form_data)
    except ValueError:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.COMMAND_TAKEN) from None
    if result is None:
        raise _not_found()
    return result


@router.post("/id/{prompt_id}/update/version", response_model=Optional[PromptModel])
async def set_prompt_version(
    prompt_id: str, form_data: PromptVersionUpdateForm, user=Depends(get_verified_user)
):
    prompt = _get(prompt_id)
    _require(prompt, user, "write")
    result = Prompts.select_version(prompt_id, form_data.version_id)
    if result is None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT())
    return result


@router.post("/id/{prompt_id}/toggle", response_model=Optional[PromptModel])
async def toggle_prompt_active(prompt_id: str, user=Depends(get_verified_user)):
    prompt = _get(prompt_id)
    _require(prompt, user, "write")
    result = Prompts.toggle(prompt_id)
    if result is None:
        raise _not_found()
    return result


@router.post("/id/{prompt_id}/access/update", response_model=Optional[PromptModel])
async def update_prompt_access_by_id(
    request: Request,
    prompt_id: str,
    form_data: PromptAccessGrantsForm,
    user=Depends(get_verified_user),
):
    prompt = _get(prompt_id)
    if user.role != "admin" and prompt.user_id != user.id:
        raise _denied()
    # The inherited permission schema has no ``sharing.prompts`` key. Owner
    # control is therefore the common measured baseline; a later permission
    # schema slice can add the newer opt-out without making sharing unusable.
    _ = request
    try:
        result = Prompts.replace_grants(prompt_id, form_data.access_grants)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid access grant") from None
    if result is None:
        raise _not_found()
    return result


@router.delete("/id/{prompt_id}/delete", response_model=bool)
async def delete_prompt_by_id(prompt_id: str, user=Depends(get_verified_user)):
    prompt = _get(prompt_id)
    if user.role != "admin" and prompt.user_id != user.id:
        raise _denied()
    return Prompts.delete_prompt_by_id(prompt_id)


@router.get("/id/{prompt_id}/history", response_model=list[PromptHistoryResponse])
async def get_prompt_history(prompt_id: str, page: int = 0, user=Depends(get_verified_user)):
    """Get version history for a prompt."""
    prompt = _get(prompt_id)
    _require(prompt, user, "read")
    return Prompts.get_history(prompt_id, page)


@router.get("/id/{prompt_id}/history/diff")
async def get_prompt_diff(
    prompt_id: str, from_id: str, to_id: str, user=Depends(get_verified_user)
):
    """Get diff between two versions."""
    prompt = _get(prompt_id)
    _require(prompt, user, "read")
    source = Prompts.get_history_entry(prompt_id, from_id)
    target = Prompts.get_history_entry(prompt_id, to_id)
    if source is None or target is None:
        raise _not_found()
    from_content = str(source.snapshot.get("content", ""))
    to_content = str(target.snapshot.get("content", ""))
    content_diff = list(difflib.unified_diff(
        from_content.splitlines(), to_content.splitlines(),
        fromfile=f"v{from_id[:8]}", tofile=f"v{to_id[:8]}", lineterm="",
    ))
    return {
        "from_id": from_id, "to_id": to_id,
        "from_snapshot": source.snapshot, "to_snapshot": target.snapshot,
        "content_diff": content_diff,
        "line_endings_only": from_content != to_content and from_content.splitlines() == to_content.splitlines(),
        "name_changed": source.snapshot.get("name") != target.snapshot.get("name"),
    }


@router.get("/id/{prompt_id}/history/{history_id}", response_model=PromptHistoryModel)
async def get_prompt_history_entry(
    prompt_id: str, history_id: str, user=Depends(get_verified_user)
):
    """Get a specific version from history."""
    prompt = _get(prompt_id)
    _require(prompt, user, "read")
    result = Prompts.get_history_entry(prompt_id, history_id)
    if result is None:
        raise _not_found()
    return result


@router.delete("/id/{prompt_id}/history/{history_id}", response_model=bool)
async def delete_prompt_history_entry(
    prompt_id: str, history_id: str, user=Depends(get_verified_user)
):
    """Delete a history entry. Cannot delete the active production version."""
    prompt = _get(prompt_id)
    if user.role != "admin" and prompt.user_id != user.id:
        raise _denied()
    if Prompts.get_history_entry(prompt_id, history_id) is None:
        raise _not_found()
    if prompt.version_id == history_id:
        raise HTTPException(status_code=400, detail="Cannot delete the active version")
    return Prompts.delete_history_entry(prompt_id, history_id)


@router.get("/command/{command}", response_model=Optional[PromptResponse])
async def get_prompt_by_command(command: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_command(f"/{command}")
    if prompt is None:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.NOT_FOUND)
    if user.role == "admin" or prompt.user_id == user.id or Prompts.has_grant(prompt.access_grants, user.id, "read"):
        return prompt
    raise _denied()


@router.post("/command/{command}/update", response_model=Optional[PromptResponse])
async def update_prompt_by_command(command: str, form_data: PromptForm, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_command(f"/{command}")
    if prompt is None:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.NOT_FOUND)
    if user.role != "admin" and prompt.user_id != user.id and not Prompts.has_grant(prompt.access_grants, user.id, "write"):
        raise _denied()
    result = Prompts.update_prompt_by_command(f"/{command}", form_data)
    if result is None:
        raise _denied()
    return result


@router.delete("/command/{command}/delete", response_model=bool)
async def delete_prompt_by_command(command: str, user=Depends(get_verified_user)):
    prompt = Prompts.get_prompt_by_command(f"/{command}")
    if prompt is None:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.NOT_FOUND)
    if user.role != "admin" and prompt.user_id != user.id and not Prompts.has_grant(prompt.access_grants, user.id, "write"):
        raise _denied()
    return Prompts.delete_prompt_by_command(f"/{command}")
