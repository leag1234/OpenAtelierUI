import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field
import mimetypes


from open_webui.models.folders import (
    FolderForm,
    FolderModel,
    FolderResponse,
    Folders,
)
from open_webui.models.chats import Chats
from open_webui.models.users import Users

from open_webui.config import UPLOAD_DIR
from open_webui.env import SRC_LOG_LEVELS
from open_webui.constants import ERROR_MESSAGES


from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
    Request,
)
from fastapi.responses import FileResponse, StreamingResponse


from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])


router = APIRouter()


def _not_found():
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=ERROR_MESSAGES.NOT_FOUND,
    )


def _denied():
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


def _folder_or_404(folder_id: str):
    folder = Folders.get_folder_by_id(folder_id)
    if folder is None:
        raise _not_found()
    return folder


def _sharing_enabled(request: Request, user) -> bool:
    return user.role == "admin" or has_permission(
        user.id, "sharing.folders", request.app.state.config.USER_PERMISSIONS
    )


def _require_sharing(request: Request, user) -> None:
    if not _sharing_enabled(request, user):
        raise _denied()


def _folder_permission(folder, user) -> Optional[str]:
    if user.role == "admin" or folder.user_id == user.id:
        return "write"
    return Folders.permission_for_user(folder.id, user.id)


def _folder_response(folder, user, *, include_write_access: bool = True):
    value = {
        **folder.model_dump(),
        "access_grants": Folders.get_access_grants(folder.id),
    }
    if include_write_access:
        value["write_access"] = _folder_permission(folder, user) == "write"
    return value


############################
# Get Folders
############################


@router.get("/", response_model=list[FolderModel])
async def get_folders(user=Depends(get_verified_user)):
    folders = Folders.get_folders_by_user_id(user.id)

    return [
        {
            **folder.model_dump(),
            "items": {
                "chats": [
                    {"title": chat.title, "id": chat.id}
                    for chat in Chats.get_chats_by_folder_id_and_user_id(
                        folder.id, user.id
                    )
                ]
            },
        }
        for folder in folders
    ]


############################
# Get Shared Folders
############################


@router.get("/shared")
async def get_shared_folders(request: Request, user=Depends(get_verified_user)):
    """Get folders shared with or by the current user."""
    _require_sharing(request, user)
    result = []
    for folder, permission in Folders.get_shared_folders_by_user_id(user.id):
        owner = Users.get_user_by_id(folder.user_id)
        result.append(
            {
                **folder.model_dump(),
                "owner_name": owner.name if owner else None,
                "permission": permission,
            }
        )
    return result


############################
# Create Folder
############################


@router.post("/")
def create_folder(form_data: FolderForm, user=Depends(get_verified_user)):
    parent_id = form_data.parent_id
    if parent_id is not None and not Folders.get_folder_by_id_and_user_id(
        parent_id, user.id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    folder = Folders.get_folder_by_parent_id_and_user_id_and_name(
        parent_id, user.id, form_data.name
    )

    if folder:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Folder already exists"),
        )

    try:
        folder = Folders.insert_new_folder(user.id, form_data.name, parent_id)
        return folder
    except Exception as e:
        log.exception(e)
        log.error("Error creating folder")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT("Error creating folder"),
        )


############################
# Get Folders By Id
############################


@router.get("/{id}", response_model=Optional[FolderResponse])
async def get_folder_by_id(id: str, user=Depends(get_verified_user)):
    folder = Folders.get_folder_by_id_and_user_id(id, user.id)
    if folder:
        return _folder_response(folder, user)
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Shared Folder Access
############################


class FolderAccessGrantsForm(BaseModel):
    access_grants: list[dict] = Field(
        json_schema_extra={"items": {"additionalProperties": True, "type": "object"}}
    )
    share_mode: Optional[Literal["continue"]] = None


@router.post("/{id}/access/update")
async def update_folder_access_by_id(
    request: Request,
    id: str,
    form_data: FolderAccessGrantsForm,
    user=Depends(get_verified_user),
):
    folder = _folder_or_404(id)
    _require_sharing(request, user)
    if user.role != "admin" and folder.user_id != user.id:
        raise _denied()
    try:
        grants = Folders.replace_access_grants(id, form_data.access_grants)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {**folder.model_dump(), "access_grants": grants}


@router.get("/{id}/shared/chats")
async def get_shared_folder_chats(
    request: Request,
    id: str,
    page: Optional[int] = Query(default=None, ge=1),
    sort_by: str = "unread_updated_at",
    sort_dir: str = "desc",
    user=Depends(get_verified_user),
):
    """Get chats within a shared folder. Returns readonly flag based on permission."""
    folder = _folder_or_404(id)
    _require_sharing(request, user)
    permission = _folder_permission(folder, user)
    if permission not in ("read", "write"):
        raise _denied()

    owner = Users.get_user_by_id(folder.user_id)
    values = Chats.get_chat_title_id_list_by_folder_id_and_user_id(
        folder.id,
        folder.user_id,
        page=page or 1,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    total = len(Chats.get_chats_by_folder_id_and_user_id(folder.id, folder.user_id))
    read_at = Folders.get_folder_read_at(folder.id, user.id)
    chats = []
    for chat in values:
        last_read_at = read_at if read_at is not None else chat.last_read_at
        if last_read_at is None:
            last_read_at = chat.created_at
        chats.append(
            {
                "id": chat.id,
                "user_id": folder.user_id,
                "title": chat.title,
                "updated_at": chat.updated_at,
                "created_at": chat.created_at,
                "last_read_at": last_read_at,
                "active": chat.updated_at > last_read_at,
                "owner_name": owner.name if owner else None,
                "participants": (
                    [{"id": folder.user_id, "name": owner.name}] if owner else []
                ),
                "readonly": user.id != folder.user_id,
            }
        )
    return {
        "chats": chats,
        "folder_permission": permission,
        "has_more": total > (page or 1) * 60,
        "total": total,
    }


@router.post("/{id}/read")
async def mark_folder_chats_read_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    folder = _folder_or_404(id)
    _require_sharing(request, user)
    permission = _folder_permission(folder, user)
    if user.role != "admin" and folder.user_id != user.id and permission != "read":
        raise _denied()

    read_at = int(time.time())
    Folders.mark_folder_read(folder.id, user.id, read_at)
    chats = Chats.get_chats_by_folder_id_and_user_id(folder.id, folder.user_id)
    if folder.user_id == user.id:
        unread_counts = {
            item.id: sum(
                chat.updated_at > (Folders.get_folder_read_at(item.id, user.id) or 0)
                for chat in Chats.get_chats_by_folder_id_and_user_id(item.id, user.id)
            )
            for item in Folders.get_folders_by_user_id(user.id)
        }
        updated_count = len(chats)
    else:
        unread_counts = {}
        updated_count = 0
    return {
        "folder_id": folder.id,
        "folder_ids": [folder.id],
        "folder_unread_counts": unread_counts,
        "updated_count": updated_count,
    }


############################
# Update Folder Name By Id
############################


@router.post("/{id}/update")
async def update_folder_name_by_id(
    id: str, form_data: FolderForm, user=Depends(get_verified_user)
):
    folder = Folders.get_folder_by_id_and_user_id(id, user.id)
    owner_id = user.id
    if folder is None and user.role == "admin":
        folder = Folders.get_folder_by_id(id)
        if folder is not None:
            owner_id = folder.user_id
    if folder:
        existing_folder = Folders.get_folder_by_parent_id_and_user_id_and_name(
            folder.parent_id, owner_id, form_data.name
        )
        if existing_folder:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Folder already exists"),
            )

        try:
            folder = Folders.update_folder_name_by_id_and_user_id(
                id, owner_id, form_data.name
            )

            return folder
        except Exception as e:
            log.exception(e)
            log.error(f"Error updating folder: {id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating folder"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update Folder Parent Id By Id
############################


class FolderParentIdForm(BaseModel):
    parent_id: Optional[str] = None


@router.post("/{id}/update/parent")
async def update_folder_parent_id_by_id(
    id: str, form_data: FolderParentIdForm, user=Depends(get_verified_user)
):
    folder = Folders.get_folder_by_id_and_user_id(id, user.id)
    if folder:
        existing_folder = Folders.get_folder_by_parent_id_and_user_id_and_name(
            form_data.parent_id, user.id, folder.name
        )

        if existing_folder:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Folder already exists"),
            )

        try:
            folder = Folders.update_folder_parent_id_by_id_and_user_id(
                id, user.id, form_data.parent_id
            )
            return folder
        except Exception as e:
            log.exception(e)
            log.error(f"Error updating folder: {id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating folder"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Update Folder Is Expanded By Id
############################


class FolderIsExpandedForm(BaseModel):
    is_expanded: bool


@router.post("/{id}/update/expanded")
async def update_folder_is_expanded_by_id(
    id: str, form_data: FolderIsExpandedForm, user=Depends(get_verified_user)
):
    folder = Folders.get_folder_by_id_and_user_id(id, user.id)
    if folder:
        try:
            folder = Folders.update_folder_is_expanded_by_id_and_user_id(
                id, user.id, form_data.is_expanded
            )
            return folder
        except Exception as e:
            log.exception(e)
            log.error(f"Error updating folder: {id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating folder"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# Delete Folder By Id
############################


@router.delete("/{id}")
async def delete_folder_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    folder = Folders.get_folder_by_id(id)
    if folder is not None and user.role != "admin" and folder.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    chat_delete_permission = has_permission(
        user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
    )

    if user.role != "admin" and not chat_delete_permission:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    owner_id = (
        folder.user_id if folder is not None and user.role == "admin" else user.id
    )
    folder = Folders.get_folder_by_id_and_user_id(id, owner_id)
    if folder:
        try:
            result = Folders.delete_folder_by_id_and_user_id(id, owner_id)
            if result:
                return result
            else:
                raise Exception("Error deleting folder")
        except Exception as e:
            log.exception(e)
            log.error(f"Error deleting folder: {id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error deleting folder"),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
