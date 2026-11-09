import base64
import binascii
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from open_webui.models.auths import Auths
from open_webui.models.groups import Groups
from open_webui.models.chats import Chats
from open_webui.models.users import (
    UserModel,
    UserRoleUpdateForm,
    Users,
    UserSettings,
    UserUpdateForm,
)


from open_webui.socket.main import disconnect_user_sockets, get_active_status_by_user_id
from open_webui.config import DEFAULT_USER_PERMISSIONS
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import FRONTEND_BUILD_DIR, SRC_LOG_LEVELS, STATIC_DIR
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from open_webui.utils.auth import (
    get_admin_user,
    get_password_hash,
    get_verified_user,
    revoke_user_session_epoch,
)
from open_webui.utils.access_control import get_permissions
from open_webui.utils.user_profile_compat import (
    UserStatus,
    UserUsageResponse,
    UserVariablesForm,
    UserVariablesResponse,
    build_usage,
    merge_public_info,
    public_info,
    read_variables,
    status_projection,
    update_status_info,
    update_variables_info,
    validate_variables,
)


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


class UserInfoResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    bio: Optional[str] = None
    groups: Optional[list] = None
    is_active: bool = True
    status_emoji: Optional[str] = None
    status_message: Optional[str] = None
    status_expires_at: Optional[int] = None
    timezone: Optional[str] = None
    last_active_at: Optional[int] = None


class UserInfoListResponse(BaseModel):
    users: list[UserInfoResponse]
    total: int


def _user_info_projection(user):
    info = user.info if isinstance(user.info, dict) else {}
    groups = Groups.get_groups_by_member_id(user.id)
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "bio": getattr(user, "bio", None) or info.get("bio"),
        "groups": [getattr(group, "id", group) for group in groups],
        "is_active": True,
        "status_emoji": info.get("status_emoji"),
        "status_message": info.get("status_message"),
        "status_expires_at": info.get("status_expires_at"),
        "timezone": (
            getattr(user, "settings", None).model_dump().get("timezone")
            if hasattr(getattr(user, "settings", None), "model_dump")
            else (getattr(user, "settings", None) or {}).get("timezone")
            if isinstance(getattr(user, "settings", None), dict)
            else None
        ),
        "last_active_at": getattr(user, "last_active_at", None),
    }


def _user_active(user_id: str) -> bool:
    if get_active_status_by_user_id(user_id):
        return True
    current = Users.get_user_by_id(user_id)
    return bool(
        current
        and isinstance(current.last_active_at, int)
        and current.last_active_at >= int(time.time()) - 300
    )


def _user_group_projection(group):
    return {
        "id": group.id,
        "user_id": group.user_id,
        "name": group.name,
        "description": group.description,
        "data": group.data,
        "meta": group.meta,
        "permissions": group.permissions,
        "parent_group_id": None,
        "created_at": group.created_at,
        "updated_at": group.updated_at,
    }


############################
# GetUsers
############################


@router.get(
    "/",
    response_model=list[UserModel],
    response_model_exclude={"__all__": {"api_key"}},
)
async def get_users(
    skip: Optional[int] = None,
    limit: Optional[int] = None,
    user=Depends(get_admin_user),
):
    return Users.get_users(skip, limit)


@router.get("/search", response_model=UserInfoListResponse)
async def search_users(
    query: str = "",
    order_by: str = "created_at",
    direction: str = "asc",
    page: int = 1,
    user=Depends(get_verified_user),
):
    if len(query) > 512:
        raise HTTPException(status_code=422, detail="Search query is too long")
    try:
        users, count = Users.search_users(query, order_by, direction, page)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    items = []
    for item in users:
        projected = _user_info_projection(item)
        projected["is_active"] = _user_active(item.id)
        items.append(projected)
    return {"users": items, "total": count}


@router.get("/all", response_model=UserInfoListResponse)
async def get_users_all(user=Depends(get_admin_user)):
    users = Users.get_users()
    return {
        "users": [_user_info_projection(item) for item in users],
        "total": len(users),
    }


############################
# User Groups
############################


@router.get("/groups")
async def get_user_groups(user=Depends(get_verified_user)):
    return Groups.get_groups_by_member_id(user.id)


############################
# User Permissions
############################


@router.get("/permissions")
async def get_user_permissisions(request: Request, user=Depends(get_verified_user)):
    user_permissions = get_permissions(
        user.id, request.app.state.config.USER_PERMISSIONS
    )

    return user_permissions


############################
# User Default Permissions
############################
class WorkspacePermissions(BaseModel):
    models: bool = False
    models_export: bool = False
    models_hidden: bool = False
    models_import: bool = False
    knowledge: bool = False
    prompts: bool = False
    prompts_export: bool = False
    prompts_import: bool = False
    skills: bool = False
    skills_export: bool = False
    skills_import: bool = False
    tools: bool = False
    tools_export: bool = False
    tools_import: bool = False


class SharingPermissions(BaseModel):
    folders: bool = False
    knowledge: bool = False
    models: bool = False
    notes: bool = False
    open_chats: bool = False
    prompts: bool = False
    public_calendars: bool = False
    public_chats: bool = False
    public_models: bool = True
    public_knowledge: bool = True
    public_notes: bool = False
    public_prompts: bool = True
    skills: bool = False
    public_skills: bool = False
    tools: bool = False
    public_tools: bool = True


class ChatPermissions(BaseModel):
    continue_response: bool = True
    controls: bool = True
    file_upload: bool = True
    delete: bool = True
    delete_message: bool = True
    edit: bool = True
    export: bool = True
    import_: bool = Field(default=True, alias="import")
    params: bool = True
    rate_response: bool = True
    regenerate_response: bool = True
    share: bool = True
    stt: bool = True
    system_prompt: bool = True
    tts: bool = True
    call: bool = True
    multiple_models: bool = True
    temporary: bool = True
    temporary_enforced: bool = False
    valves: bool = True
    web_upload: bool = True


class FeaturesPermissions(BaseModel):
    api_keys: bool = False
    automations: bool = False
    calendar: bool = True
    channels: bool = True
    direct_tool_servers: bool = False
    encrypted_channels: bool = False
    folders: bool = True
    web_search: bool = True
    image_generation: bool = True
    code_interpreter: bool = True
    memories: bool = True
    notes: bool = True
    webhooks: bool = False


class SettingsPermissions(BaseModel):
    interface: bool = True


class AccessGrantPermissions(BaseModel):
    allow_groups: bool = True
    allow_users: bool = True


class UserPermissions(BaseModel):
    workspace: WorkspacePermissions
    sharing: SharingPermissions
    chat: ChatPermissions
    features: FeaturesPermissions
    settings: SettingsPermissions = Field(default_factory=SettingsPermissions)
    access_grants: AccessGrantPermissions = Field(default_factory=AccessGrantPermissions)


@router.get("/default/permissions", response_model=UserPermissions)
async def get_default_user_permissions(request: Request, user=Depends(get_admin_user)):
    return {
        "workspace": WorkspacePermissions(
            **request.app.state.config.USER_PERMISSIONS.get("workspace", {})
        ),
        "sharing": SharingPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("sharing", {})
        ),
        "chat": ChatPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("chat", {})
        ),
        "features": FeaturesPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("features", {})
        ),
        "settings": SettingsPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("settings", {})
        ),
        "access_grants": AccessGrantPermissions(
            **request.app.state.config.USER_PERMISSIONS.get("access_grants", {})
        ),
    }


@router.get("/default/permissions/defaults", response_model=UserPermissions)
async def get_default_user_permissions_defaults(user=Depends(get_admin_user)):
    return DEFAULT_USER_PERMISSIONS


@router.post("/default/permissions")
async def update_default_user_permissions(
    request: Request, form_data: UserPermissions, user=Depends(get_admin_user)
):
    request.app.state.config.USER_PERMISSIONS = form_data.model_dump(by_alias=True)
    return request.app.state.config.USER_PERMISSIONS


############################
# UpdateUserRole
############################


@router.post(
    "/update/role",
    response_model=Optional[UserModel],
    response_model_exclude={"api_key"},
)
async def update_user_role(form_data: UserRoleUpdateForm, user=Depends(get_admin_user)):
    if user.id != form_data.id and form_data.id != Users.get_first_user().id:
        return Users.update_user_role_by_id(form_data.id, form_data.role)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACTION_PROHIBITED,
    )


############################
# GetUserSettingsBySessionUser
############################


@router.get("/user/settings", response_model=Optional[UserSettings])
async def get_user_settings_by_session_user(user=Depends(get_verified_user)):
    user = Users.get_user_by_id(user.id)
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserSettingsBySessionUser
############################


@router.post("/user/settings/update", response_model=UserSettings)
async def update_user_settings_by_session_user(
    form_data: UserSettings, user=Depends(get_verified_user)
):
    user = Users.update_user_settings_by_id(user.id, form_data.model_dump())
    if user:
        return user.settings
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# GetUserInfoBySessionUser
############################


@router.get("/user/info", response_model=Optional[dict])
async def get_user_info_by_session_user(user=Depends(get_verified_user)):
    user = Users.get_user_by_id(user.id)
    if user:
        return public_info(user)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserInfoBySessionUser
############################


@router.post("/user/info/update", response_model=Optional[dict])
async def update_user_info_by_session_user(
    form_data: dict, user=Depends(get_verified_user)
):
    user = Users.get_user_by_id(user.id)
    if user:
        try:
            updated_info = merge_public_info(user.info, form_data)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        user = Users.update_user_by_id(user.id, {"info": updated_info})
        if user:
            return public_info(user)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.USER_NOT_FOUND,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# Current user profile compatibility
############################


@router.get("/usage", response_model=UserUsageResponse)
async def get_user_usage_by_session_user(
    days: int = Query(30, ge=7, le=732),
    start_date: Optional[int] = None,
    end_date: Optional[int] = None,
    user=Depends(get_verified_user),
):
    # The public contract permits explicit bounds. Preserve the requested day
    # span while keeping the calculation local to this user's stored chats.
    if start_date is not None and end_date is not None:
        if start_date > end_date:
            raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
        days = max(7, min(732, ((end_date - start_date) // 86400) or 1))
    return build_usage(Chats.get_chats_by_user_id(user.id), days, end_date)


@router.get("/user/status")
async def get_user_status_by_session_user(user=Depends(get_verified_user)):
    current = Users.get_user_by_id(user.id)
    if current:
        return status_projection(current)
    raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)


@router.post("/user/status/update")
async def update_user_status_by_session_user(
    form_data: UserStatus, user=Depends(get_verified_user)
):
    current = Users.get_user_by_id(user.id)
    if current:
        updated = Users.update_user_by_id(
            user.id, {"info": update_status_info(current.info, form_data)}
        )
        if updated:
            return status_projection(updated)
    raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)


@router.get("/user/variables", response_model=UserVariablesResponse)
async def get_user_variables_by_session_user(user=Depends(get_verified_user)):
    current = Users.get_user_by_id(user.id)
    if current:
        return {"variables": read_variables(current)}
    raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)


@router.post("/user/variables/update", response_model=UserVariablesResponse)
async def update_user_variables_by_session_user(
    form_data: UserVariablesForm, user=Depends(get_verified_user)
):
    current = Users.get_user_by_id(user.id)
    if not current:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)
    existing = read_variables(current)
    try:
        variables = validate_variables(form_data.variables, existing)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    updated = Users.update_user_by_id(
        user.id, {"info": update_variables_info(current.info, variables)}
    )
    if updated:
        return {"variables": read_variables(updated)}
    raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)


############################
# User profile lookup compatibility
############################


@router.get("/{user_id}/active")
async def get_user_active_status_by_id(
    user_id: str, user=Depends(get_verified_user)
):
    return {"active": _user_active(user_id)}


@router.get("/{user_id}/groups")
async def get_user_groups_by_id(user_id: str, user=Depends(get_admin_user)):
    return [_user_group_projection(group) for group in Groups.get_groups_by_member_id(user_id)]


@router.get("/{user_id}/info", response_model=UserInfoResponse)
async def get_user_info_by_id(user_id: str, user=Depends(get_verified_user)):
    current = Users.get_user_by_id(user_id)
    if current:
        projected = _user_info_projection(current)
        projected["is_active"] = _user_active(user_id)
        return projected
    raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)


@router.get("/{user_id}/oauth/sessions")
async def get_user_oauth_sessions_by_id(user_id: str, user=Depends(get_admin_user)):
    current = Users.get_user_by_id(user_id)
    if not current or not current.oauth_sub:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.NOT_FOUND)
    # The BSD base has no external OAuth-session store. An OAuth-linked user
    # without provider session metadata is represented as an empty local list.
    return []


@router.get("/{user_id}/preview")
async def get_user_preview(user_id: str, user=Depends(get_admin_user)):
    current = Users.get_user_by_id(user_id)
    if not current:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    groups = Groups.get_groups_by_member_id(user_id)
    return {
        "user": {"id": current.id, "name": current.name},
        "groups": [{"id": group.id, "name": group.name} for group in groups],
        "models": {"items": [], "total": 0},
        "knowledge": {"items": [], "total": 0},
        "tools": {"items": [], "total": 0},
    }


@router.get("/{user_id}/profile/image")
async def get_user_profile_image_by_id(
    user_id: str, user=Depends(get_verified_user)
):
    current = Users.get_user_by_id(user_id)
    if not current:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.USER_NOT_FOUND)
    image_url = current.profile_image_url or "/user.png"
    if image_url.startswith("data:image/"):
        try:
            header, encoded = image_url.split(",", 1)
            if not header.endswith(";base64"):
                raise ValueError("unsupported image encoding")
            content = base64.b64decode(encoded, validate=True)
            if len(content) > 2 * 1024 * 1024:
                raise ValueError("image is too large")
            return Response(content=content, media_type=header[5:-7])
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="Invalid user profile image.")
    roots = []
    relative = None
    if image_url == "/user.png":
        roots, relative = [Path(FRONTEND_BUILD_DIR).resolve()], "user.png"
    elif image_url.startswith("/static/"):
        roots, relative = [Path(STATIC_DIR).resolve()], unquote(image_url[len("/static/") :])
    if relative is not None:
        for root in roots:
            image_path = (root / relative).resolve()
            try:
                image_path.relative_to(root)
            except ValueError:
                continue
            if image_path.is_file():
                return FileResponse(image_path)
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    if image_url.startswith(("/", "https://", "http://")):
        return RedirectResponse(image_url)
    raise HTTPException(status_code=400, detail="Invalid user profile image.")


############################
# GetUserById
############################


class UserResponse(BaseModel):
    name: str
    profile_image_url: str
    active: Optional[bool] = None


@router.post("/{user_id}/sessions/revoke")
async def revoke_user_sessions(user_id: str, user=Depends(get_admin_user)):
    revoke_user_session_epoch(user_id)
    await disconnect_user_sockets(user_id)
    return None


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(user_id: str, user=Depends(get_verified_user)):
    # Check if user_id is a shared chat
    # If it is, get the user_id from the chat
    if user_id.startswith("shared-"):
        chat_id = user_id.replace("shared-", "")
        chat = Chats.get_chat_by_id(chat_id)
        if chat:
            user_id = chat.user_id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.USER_NOT_FOUND,
            )

    user = Users.get_user_by_id(user_id)

    if user:
        return UserResponse(
            **{
                "name": user.name,
                "profile_image_url": user.profile_image_url,
                "active": get_active_status_by_user_id(user_id),
            }
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.USER_NOT_FOUND,
        )


############################
# UpdateUserById
############################


@router.post(
    "/{user_id}/update",
    response_model=Optional[UserModel],
    response_model_exclude={"api_key"},
)
async def update_user_by_id(
    user_id: str,
    form_data: UserUpdateForm,
    session_user=Depends(get_admin_user),
):
    user = Users.get_user_by_id(user_id)

    if user:
        if form_data.role is not None:
            if form_data.role not in {"admin", "user", "pending"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Unsupported user role",
                )
            first_user = Users.get_first_user()
            if (
                first_user is not None
                and user_id == first_user.id
                and form_data.role != user.role
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ERROR_MESSAGES.ACTION_PROHIBITED,
                )

        if form_data.email.lower() != user.email:
            email_user = Users.get_user_by_email(form_data.email.lower())
            if email_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.EMAIL_TAKEN,
                )

        if form_data.password:
            hashed = get_password_hash(form_data.password)
            log.debug(f"hashed: {hashed}")
            Auths.update_user_password_by_id(user_id, hashed)

        Auths.update_email_by_id(user_id, form_data.email.lower())
        updated_user = Users.update_user_by_id(
            user_id,
            {
                "name": form_data.name,
                "email": form_data.email.lower(),
                "profile_image_url": form_data.profile_image_url,
                **({"role": form_data.role} if form_data.role is not None else {}),
            },
        )

        if updated_user:
            return updated_user

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(),
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.USER_NOT_FOUND,
    )


############################
# DeleteUserById
############################


@router.delete("/{user_id}", response_model=bool)
async def delete_user_by_id(user_id: str, user=Depends(get_admin_user)):
    if user.id != user_id:
        result = Auths.delete_auth_by_id(user_id)

        if result:
            return True

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DELETE_USER_ERROR,
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACTION_PROHIBITED,
    )
