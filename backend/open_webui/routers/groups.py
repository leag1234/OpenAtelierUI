import os
from pathlib import Path
from typing import Literal, Optional
import logging

from open_webui.models.users import Users
from open_webui.models.groups import (
    Groups,
    GroupForm,
    GroupUpdateForm,
    GroupResponse,
    CurrentGroupResponse,
    GroupMembersResponse,
    GroupUserIdsForm,
)

from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.env import SRC_LOG_LEVELS
from open_webui.utils.group_compat import project_group_members, project_group_response


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


@router.post("/create", response_model=Optional[GroupResponse])
async def create_new_group(form_data: GroupForm, user=Depends(get_admin_user)):
    try:
        group = Groups.insert_new_group(user.id, form_data)
        if group:
            return group
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


@router.post("/id/{id}/update", response_model=Optional[GroupResponse])
async def update_group_by_id(
    id: str, form_data: GroupUpdateForm, user=Depends(get_admin_user)
):
    try:
        if form_data.user_ids:
            form_data.user_ids = Users.get_valid_user_ids(form_data.user_ids)

        group = Groups.update_group_by_id(id, form_data)
        if group:
            return group
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
