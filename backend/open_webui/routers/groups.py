import os
from pathlib import Path
from typing import Literal, Optional
import logging

from open_webui.models.users import Users
from open_webui.models.models import Models
from open_webui.models.tools import Tools
from open_webui.models.knowledge import Knowledges
from open_webui.models.groups import (
    Groups,
    GroupForm,
    GroupUpdateForm,
    GroupResponse,
    CurrentGroupResponse,
    GroupMembersResponse,
    GroupUserIdsForm,
    GroupExportResponse,
    GroupInfoResponse,
)

from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.env import SRC_LOG_LEVELS
from open_webui.routers.users import UserInfoResponse
from open_webui.utils.group_compat import (
    project_group_export,
    project_group_info,
    project_group_members,
    project_group_preview,
    project_group_response,
)


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

############################
# GetFunctions
############################


@router.get("/", response_model=list[GroupResponse])
async def get_groups(user=Depends(get_verified_user)):
    if user.role == "admin":
        return Groups.get_groups()
    else:
        return Groups.get_groups_by_member_id(user.id)


############################
# CreateNewGroup
############################


@router.post("/create", response_model=Optional[CurrentGroupResponse])
async def create_new_group(form_data: GroupForm, user=Depends(get_admin_user)):
    try:
        group = Groups.insert_new_group(user.id, form_data)
        if group:
            return project_group_response(group)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error creating group"),
            )
    except Exception as e:
        log.exception(f"Error creating a new group: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# GetGroupById
############################


@router.get("/id/{id}", response_model=Optional[CurrentGroupResponse])
async def get_group_by_id(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return project_group_response(group)
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We could not find what you're looking for :/",
        )


@router.get("/id/{id}/export", response_model=Optional[GroupExportResponse])
async def export_group_by_id(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return project_group_export(group)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="We could not find what you're looking for :/",
    )


@router.get("/id/{id}/info", response_model=Optional[GroupInfoResponse])
async def get_group_info_by_id(id: str, user=Depends(get_verified_user)):
    group = Groups.get_group_by_id(id)
    if group:
        return project_group_info(group)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="We could not find what you're looking for :/",
    )


@router.get("/id/{id}/preview")
async def preview_group_access(
    request: Request, id: str, user=Depends(get_admin_user)
):
    """Show what resources a group can access (preview audit)."""
    group = Groups.get_group_by_id(id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="We could not find what you're looking for :/",
        )
    return project_group_preview(
        group,
        request.app.state.config.USER_PERMISSIONS,
        Models.get_all_models(),
        Tools.get_tools(),
        Knowledges.get_knowledge_bases(),
    )


@router.post(
    "/id/{id}/users",
    response_model=list[UserInfoResponse],
    response_model_exclude={"__all__": {"last_active_at"}},
)
async def get_users_in_group(id: str, user=Depends(get_admin_user)):
    group = Groups.get_group_by_id(id)
    if group is None:
        return []
    raw_ids = group.user_ids if isinstance(group.user_ids, list) else []
    exact_ids = list(dict.fromkeys(value for value in raw_ids if isinstance(value, str)))
    users = {entry.id: entry for entry in Users.get_users_by_user_ids(exact_ids)}
    result = []
    for user_id in exact_ids:
        entry = users.get(user_id)
        if entry is None:
            continue
        settings = (
            entry.settings.model_dump()
            if hasattr(entry.settings, "model_dump")
            else entry.settings
        )
        result.append(
            {
                "id": entry.id,
                "name": entry.name,
                "email": entry.email,
                "role": entry.role,
                "bio": None,
                "groups": [],
                "is_active": False,
                "status_emoji": None,
                "status_message": None,
                "status_expires_at": None,
                "timezone": settings.get("timezone") if isinstance(settings, dict) else None,
            }
        )
    return result


@router.get("/id/{id}/members", response_model=GroupMembersResponse)
async def get_group_members(
    id: str,
    membership: Literal["direct", "inherited", "effective"] = "direct",
    query: Optional[str] = None,
    page: int = Query(1, ge=1),
    user=Depends(get_admin_user),
):
    try:
        group, users = Groups.get_group_members_snapshot(id)
        if group is None:
            raise HTTPException(status_code=404, detail="Group not found.")
        return project_group_members(group, users, membership, query, page)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _mutate_group_membership(id: str, form_data: GroupUserIdsForm, action: str):
    # Unknown user IDs are ignored by the existing validation helper, matching
    # the measured no-op behavior. The model repeats existence filtering inside
    # its transaction to avoid persisting a stale/unverified identity.
    requested = form_data.user_ids or []
    valid_ids = Users.get_valid_user_ids(list(dict.fromkeys(requested))) if requested else []
    try:
        group = Groups.mutate_group_user_ids(id, valid_ids, action)
    except Exception as e:
        log.exception("Unable to %s group membership for %s", action, id)
        raise HTTPException(status_code=500, detail="Unable to update group membership.") from e
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found.")
    return project_group_response(group)


@router.post("/id/{id}/users/add", response_model=CurrentGroupResponse)
async def add_group_users(
    id: str, form_data: GroupUserIdsForm, user=Depends(get_admin_user)
):
    return await _mutate_group_membership(id, form_data, "add")


@router.post("/id/{id}/users/remove", response_model=CurrentGroupResponse)
async def remove_group_users(
    id: str, form_data: GroupUserIdsForm, user=Depends(get_admin_user)
):
    return await _mutate_group_membership(id, form_data, "remove")


############################
# UpdateGroupById
############################


@router.post("/id/{id}/update", response_model=Optional[CurrentGroupResponse])
async def update_group_by_id(
    id: str, form_data: GroupUpdateForm, user=Depends(get_admin_user)
):
    try:
        if form_data.user_ids:
            form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)

        group = Groups.update_group_by_id(id, form_data)
        if group:
            return project_group_response(group)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating group"),
            )
    except Exception as e:
        log.exception(f"Error updating group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


############################
# DeleteGroupById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_group_by_id(id: str, user=Depends(get_admin_user)):
    try:
        result = Groups.delete_group_by_id(id)
        if result:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting group"),
            )
    except Exception as e:
        log.exception(f"Error deleting group {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )
