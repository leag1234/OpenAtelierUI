import copy
import json
import logging
from typing import Optional, Union


from open_webui.socket.main import get_event_emitter
from open_webui.models.chats import (
    ChatForm,
    ChatImportForm,
    ChatsImportForm,
    PrivateChatForm,
    ChatResponse,
    PrivateChatResponse,
    Chats,
    ChatTitleIdResponse,
    PrivateChatTitleIdResponse,
    SharedChatResponse,
    _chat_current_message_id,
    _normalize_private_chat_authors,
    _public_chat_meta,
    _project_private_chat_authors,
    _saved_work_marker,
)
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.models.tags import TagModel, Tags
from open_webui.models.folders import Folders

from open_webui.config import ENABLE_ADMIN_CHAT_ACCESS, ENABLE_ADMIN_EXPORT
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel


from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


def _saved_chat_response(chat, user, mark_continue=False, user_lookup=None):
    values = chat.model_dump()
    chat_payload = copy.deepcopy(values.get("chat") or {})
    marked = _saved_work_marker(values.get("meta")) is not None
    if marked:
        chat_payload = _project_private_chat_authors(chat_payload, user, user_lookup)
        if mark_continue:
            chat_payload["share_mode"] = "continue"
    values["chat"] = chat_payload
    values["meta"] = _public_chat_meta(values.get("meta"))
    values["variables"] = {}
    values["tasks"] = None
    values["summary"] = None
    values["context_usage"] = None
    values["current_message_id"] = _chat_current_message_id(chat_payload)
    values.setdefault("folder_id", None)
    return PrivateChatResponse(**values)


def _require_supported_saved_chat_fields(form_data):
    if form_data.variables not in (None, {}):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Non-empty chat variables are not supported by this compatibility prototype.",
        )
    if form_data.folder_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Folder chat behavior is not supported by this compatibility prototype.",
        )


############################
# GetChatList
############################


@router.get("/", response_model=list[PrivateChatTitleIdResponse])
@router.get("/list", response_model=list[PrivateChatTitleIdResponse])
async def get_session_user_chat_list(
    user=Depends(get_verified_user), page: Optional[int] = None
):
    if page is not None:
        limit = 60
        skip = (page - 1) * limit

        return Chats.get_chat_title_id_list_by_user_id(user.id, skip=skip, limit=limit)
    else:
        return Chats.get_chat_title_id_list_by_user_id(user.id)


############################
# DeleteAllChats
############################


@router.delete("/", response_model=bool)
async def delete_all_user_chats(request: Request, user=Depends(get_verified_user)):

    if user.role == "user" and not has_permission(
        user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Chats.delete_chats_by_user_id(user.id)
    return result


############################
# GetUserChatList
############################


@router.get("/list/user/{user_id}", response_model=list[ChatTitleIdResponse])
async def get_user_chat_list_by_user_id(
    user_id: str,
    user=Depends(get_admin_user),
    skip: int = 0,
    limit: int = 50,
):
    if not ENABLE_ADMIN_CHAT_ACCESS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return Chats.get_chat_list_by_user_id(
        user_id, include_archived=True, skip=skip, limit=limit
    )


############################
# CreateNewChat
############################


@router.post("/new", response_model=Optional[PrivateChatResponse])
async def create_new_chat(form_data: PrivateChatForm, user=Depends(get_verified_user)):
    _require_supported_saved_chat_fields(form_data)
    try:
        normalized = _normalize_private_chat_authors(form_data.chat, user)
        prepared = PrivateChatForm(
            chat=normalized,
            variables=form_data.variables,
            folder_id=form_data.folder_id,
        )
        chat = Chats.insert_new_chat(user.id, prepared, compatibility_saved_work=True)
        return _saved_chat_response(chat, user)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ImportChat
############################


@router.post(
    "/import", response_model=Optional[Union[ChatResponse, list[ChatResponse]]]
)
async def import_chat(
    form_data: Union[ChatImportForm, ChatsImportForm], user=Depends(get_verified_user)
):
    if isinstance(form_data, ChatsImportForm):
        try:
            chats = Chats.import_chats(
                user.id,
                [chat.model_dump(exclude_unset=True) for chat in form_data.chats],
            )
            return [ChatResponse(**chat.model_dump()) for chat in chats]
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            log.exception(e)
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT())
    try:
        chat = Chats.import_chat(user.id, form_data)
        if chat:
            tags = chat.meta.get("tags", [])
            for tag_id in tags:
                tag_id = tag_id.replace(" ", "_").lower()
                tag_name = " ".join([word.capitalize() for word in tag_id.split("_")])
                if (
                    tag_id != "none"
                    and Tags.get_tag_by_name_and_user_id(tag_name, user.id) is None
                ):
                    Tags.insert_new_tag(tag_name, user.id)

        return ChatResponse(**chat.model_dump())
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetChats
############################


@router.get("/search", response_model=list[ChatTitleIdResponse])
async def search_user_chats(
    text: str, page: Optional[int] = None, user=Depends(get_verified_user)
):
    if page is None:
        page = 1

    limit = 60
    skip = (page - 1) * limit

    chat_list = [
        ChatTitleIdResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_user_id_and_search_text(
            user.id, text, skip=skip, limit=limit
        )
    ]

    # Delete tag if no chat is found
    words = text.strip().split(" ")
    if page == 1 and len(words) == 1 and words[0].startswith("tag:"):
        tag_id = words[0].replace("tag:", "")
        if len(chat_list) == 0:
            if Tags.get_tag_by_name_and_user_id(tag_id, user.id):
                log.debug(f"deleting tag: {tag_id}")
                Tags.delete_tag_by_name_and_user_id(tag_id, user.id)

    return chat_list


############################
# GetChatsByFolderId
############################


@router.get("/folder/{folder_id}", response_model=list[ChatResponse])
async def get_chats_by_folder_id(folder_id: str, user=Depends(get_verified_user)):
    folder_ids = [folder_id]
    children_folders = Folders.get_children_folders_by_id_and_user_id(
        folder_id, user.id
    )
    if children_folders:
        folder_ids.extend([folder.id for folder in children_folders])

    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_folder_ids_and_user_id(folder_ids, user.id)
    ]


@router.get(
    "/folder/{folder_id}/list",
    response_model=list[ChatTitleIdResponse],
)
async def get_chat_list_by_folder_id(
    folder_id: str,
    page: Optional[int] = 1,
    sort_by: str = "unread_updated_at",
    sort_dir: str = "desc",
    user=Depends(get_verified_user),
):
    return Chats.get_chat_title_id_list_by_folder_id_and_user_id(
        folder_id,
        user.id,
        page=page or 1,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


############################
# GetPinnedChats
############################


@router.get("/pinned", response_model=list[ChatResponse])
async def get_user_pinned_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_pinned_chats_by_user_id(user.id)
    ]


############################
# GetChats
############################


@router.get("/all", response_model=list[ChatResponse])
async def get_user_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_chats_by_user_id(user.id)
    ]


############################
# GetArchivedChats
############################


@router.get("/all/archived", response_model=list[ChatResponse])
async def get_user_archived_chats(user=Depends(get_verified_user)):
    return [
        ChatResponse(**chat.model_dump())
        for chat in Chats.get_archived_chats_by_user_id(user.id)
    ]


############################
# GetAllTags
############################


@router.get("/all/tags", response_model=list[TagModel])
async def get_all_user_tags(user=Depends(get_verified_user)):
    try:
        tags = Tags.get_tags_by_user_id(user.id)
        return tags
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetAllChatsInDB
############################


@router.get("/all/db", response_model=list[ChatResponse])
async def get_all_user_chats_in_db(user=Depends(get_admin_user)):
    if not ENABLE_ADMIN_EXPORT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    return [ChatResponse(**chat.model_dump()) for chat in Chats.get_chats()]


############################
# GetArchivedChats
############################


@router.get("/archived", response_model=list[ChatTitleIdResponse])
async def get_archived_session_user_chat_list(
    user=Depends(get_verified_user), skip: int = 0, limit: int = 50
):
    return Chats.get_archived_chat_list_by_user_id(user.id, skip, limit)


@router.get("/archived/count", response_model=int)
async def get_archived_session_user_chat_count(user=Depends(get_verified_user)):
    return Chats.count_archived_chats_by_user_id(user.id)


############################
# ArchiveAllChats
############################


@router.post("/archive/all", response_model=bool)
async def archive_all_chats(user=Depends(get_verified_user)):
    return Chats.archive_all_chats_by_user_id(user.id)


############################
# GetSharedChatById
############################


class ChatSharedAccessForm(BaseModel):
    access_grants: list[dict]


@router.get("/shared", response_model=list[SharedChatResponse])
async def get_shared_session_user_chat_list(
    page: Optional[int] = 1,
    query: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    user=Depends(get_verified_user),
):
    return Chats.get_shared_chat_list_by_user_id(
        user.id,
        page=page or 1,
        query_text=query,
        order_by=order_by,
        direction=direction,
    )


class ChatConfigForm(BaseModel):
    ENABLE_CONTEXT_COMPACTION: bool
    CONTEXT_COMPACTION_TOKEN_THRESHOLD: int
    CONTEXT_COMPACTION_PROMPT_TEMPLATE: str
    CONTEXT_COMPACTION_MODEL: Optional[str] = ""
    CONTEXT_COMPACTION_RETENTION_PERCENTAGE: int = 40
    CONTEXT_COMPACTION_TOKEN_CAP: Optional[int] = None
    ENABLE_TOOL_PERMISSIONS: bool = False


def _chat_config_values(request: Request):
    return ChatConfigForm(
        ENABLE_CONTEXT_COMPACTION=request.app.state.config.ENABLE_CONTEXT_COMPACTION,
        CONTEXT_COMPACTION_TOKEN_THRESHOLD=request.app.state.config.CONTEXT_COMPACTION_TOKEN_THRESHOLD,
        CONTEXT_COMPACTION_PROMPT_TEMPLATE=request.app.state.config.CONTEXT_COMPACTION_PROMPT_TEMPLATE,
        CONTEXT_COMPACTION_MODEL=request.app.state.config.CONTEXT_COMPACTION_MODEL,
        CONTEXT_COMPACTION_RETENTION_PERCENTAGE=request.app.state.config.CONTEXT_COMPACTION_RETENTION_PERCENTAGE,
        CONTEXT_COMPACTION_TOKEN_CAP=request.app.state.config.CONTEXT_COMPACTION_TOKEN_CAP,
        ENABLE_TOOL_PERMISSIONS=request.app.state.config.ENABLE_TOOL_PERMISSIONS,
    )


@router.get("/config", response_model=ChatConfigForm)
async def get_chat_config(request: Request, user=Depends(get_admin_user)):
    return _chat_config_values(request)


@router.post("/config", response_model=ChatConfigForm)
async def set_chat_config(
    request: Request,
    form_data: ChatConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.ENABLE_CONTEXT_COMPACTION = (
        form_data.ENABLE_CONTEXT_COMPACTION
    )
    request.app.state.config.CONTEXT_COMPACTION_TOKEN_THRESHOLD = (
        form_data.CONTEXT_COMPACTION_TOKEN_THRESHOLD
    )
    request.app.state.config.CONTEXT_COMPACTION_PROMPT_TEMPLATE = (
        form_data.CONTEXT_COMPACTION_PROMPT_TEMPLATE
    )
    request.app.state.config.CONTEXT_COMPACTION_MODEL = (
        form_data.CONTEXT_COMPACTION_MODEL
    )
    request.app.state.config.CONTEXT_COMPACTION_RETENTION_PERCENTAGE = min(
        form_data.CONTEXT_COMPACTION_RETENTION_PERCENTAGE,
        50,
    )
    request.app.state.config.CONTEXT_COMPACTION_TOKEN_CAP = (
        form_data.CONTEXT_COMPACTION_TOKEN_CAP
    )
    request.app.state.config.ENABLE_TOOL_PERMISSIONS = form_data.ENABLE_TOOL_PERMISSIONS
    return _chat_config_values(request)


def _shared_grants(source):
    grants = (source.meta or {}).get("_oa_shared_access_grants", [])
    return (
        [grant for grant in grants if isinstance(grant, dict)]
        if isinstance(grants, list)
        else []
    )


@router.get("/shared/{id}/access")
async def get_shared_access(id: str, user=Depends(get_verified_user)):
    source = Chats.get_chat_by_id_and_user_id(id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    return _shared_grants(source)


@router.post("/shared/{id}/access/update")
async def update_shared_access(
    id: str, form_data: ChatSharedAccessForm, user=Depends(get_verified_user)
):
    source = Chats.get_chat_by_id_and_user_id(id, user.id)
    if source is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    import uuid

    grants = []
    seen = set()
    for item in form_data.access_grants:
        kind, principal, permission = (
            item.get("principal_type"),
            item.get("principal_id"),
            item.get("permission"),
        )
        if (
            kind not in {"user", "group"}
            or permission not in {"read", "write"}
            or not isinstance(principal, str)
        ):
            raise HTTPException(status_code=422, detail="Unsupported access grant")
        existing = (
            Users.get_user_by_id(principal)
            if kind == "user"
            else Groups.get_group_by_id(principal)
        )
        identity = (kind, principal, permission)
        if existing is None or identity in seen:
            continue
        seen.add(identity)
        grants.append(
            {
                "id": str(uuid.uuid4()),
                "principal_type": kind,
                "principal_id": principal,
                "permission": permission,
            }
        )
    source = Chats.update_shared_access_grants(id, user.id, grants)
    if source is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    return _saved_chat_response(source, user)


def _shared_source_read_allowed(source, user):
    if user.role == "pending":
        return False
    if source.user_id == user.id or (user.role == "admin" and ENABLE_ADMIN_CHAT_ACCESS):
        return True
    memberships = {group.id for group in Groups.get_groups_by_member_id(user.id)}
    return any(
        grant.get("permission") == "read"
        and (
            (
                grant.get("principal_type") == "user"
                and grant.get("principal_id") == user.id
            )
            or (
                grant.get("principal_type") == "group"
                and grant.get("principal_id") in memberships
            )
        )
        for grant in _shared_grants(source)
    )


def _authorized_shared_snapshot(share_id, user):
    """Resolve the source owner before exposing or cloning a legacy snapshot.

    A share token identifies a resource; it is not an access grant. Named grants
    are deliberately not inferred from an old snapshot or client chat payload.
    """
    snapshot = Chats.get_chat_by_share_id(share_id)
    if snapshot is not None and snapshot.user_id.startswith("shared-"):
        source = Chats.get_chat_by_id(snapshot.user_id[len("shared-") :])
        if source is not None and source.share_id == snapshot.id:
            if _shared_source_read_allowed(source, user):
                return snapshot
    if user.role == "admin" and ENABLE_ADMIN_CHAT_ACCESS:
        source = Chats.get_chat_by_id(share_id)
        if source is not None and not source.user_id.startswith("shared-"):
            return source
    return None


@router.get("/share/{share_id}", response_model=Optional[PrivateChatResponse])
async def get_shared_chat_by_id(share_id: str, user=Depends(get_verified_user)):
    chat = (
        None if user.role == "pending" else _authorized_shared_snapshot(share_id, user)
    )
    if chat is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )
    values = chat.model_dump()
    if chat.user_id.startswith("shared-"):
        source = Chats.get_chat_by_id(chat.user_id[len("shared-") :])
        if source is None or source.share_id != chat.id:
            raise HTTPException(status_code=401, detail=ERROR_MESSAGES.NOT_FOUND)
        values["user_id"] = source.user_id
    return _saved_chat_response(ChatResponse(**values), user)


############################
# GetChatsByTags
############################


class TagForm(BaseModel):
    name: str


class TagFilterForm(TagForm):
    skip: Optional[int] = 0
    limit: Optional[int] = 50


@router.post("/tags", response_model=list[ChatTitleIdResponse])
async def get_user_chat_list_by_tag_name(
    form_data: TagFilterForm, user=Depends(get_verified_user)
):
    chats = Chats.get_chat_list_by_user_id_and_tag_name(
        user.id, form_data.name, form_data.skip, form_data.limit
    )
    if len(chats) == 0:
        Tags.delete_tag_by_name_and_user_id(form_data.name, user.id)

    return chats


############################
# GetChatById
############################


@router.post("/read")
async def acknowledge_private_chat_reads(user=Depends(get_verified_user)):
    result = Chats.acknowledge_private_saved_chats(user.id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Read acknowledgement is unavailable for unsupported saved chats.",
        )
    return result


@router.get("/{id}", response_model=Optional[PrivateChatResponse])
async def get_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat is None:
        candidate = Chats.get_chat_by_id(id)
        if candidate is not None and _shared_source_read_allowed(candidate, user):
            chat = candidate

    if chat:
        response_user = (
            Users.get_user_by_id(chat.user_id) if chat.user_id != user.id else user
        )
        return _saved_chat_response(
            chat,
            response_user or user,
            mark_continue=True,
            user_lookup=Users.get_user_by_id,
        )

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# UpdateChatById
############################


@router.post("/{id}", response_model=Optional[PrivateChatResponse])
async def update_chat_by_id(
    id: str, form_data: PrivateChatForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        _require_supported_saved_chat_fields(form_data)
        updated_chat = {**chat.chat, **form_data.chat}
        marked_private = (
            _saved_work_marker(chat.meta) is not None
            and chat.share_id is None
            and chat.folder_id is None
        )
        if _saved_work_marker(chat.meta) is not None:
            updated_chat = _normalize_private_chat_authors(updated_chat, user)
        title_only_update = marked_private and set(form_data.chat) == {"title"}
        chat = Chats.update_chat_by_id(
            id, updated_chat, update_timestamp=not title_only_update
        )
        return _saved_chat_response(chat, user)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# UpdateChatMessageById
############################
class MessageForm(BaseModel):
    content: str


@router.post("/{id}/messages/{message_id}", response_model=Optional[ChatResponse])
async def update_chat_message_by_id(
    id: str, message_id: str, form_data: MessageForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id(id)

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if chat.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    chat = Chats.upsert_message_to_chat_by_id_and_message_id(
        id,
        message_id,
        {
            "content": form_data.content,
        },
    )

    event_emitter = get_event_emitter(
        {
            "user_id": user.id,
            "chat_id": id,
            "message_id": message_id,
        },
        False,
    )

    if event_emitter:
        await event_emitter(
            {
                "type": "chat:message",
                "data": {
                    "chat_id": id,
                    "message_id": message_id,
                    "content": form_data.content,
                },
            }
        )

    return ChatResponse(**chat.model_dump())


############################
# SendChatMessageEventById
############################
class EventForm(BaseModel):
    type: str
    data: dict


@router.post("/{id}/messages/{message_id}/event", response_model=Optional[bool])
async def send_chat_message_event_by_id(
    id: str, message_id: str, form_data: EventForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id(id)

    if not chat:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if chat.user_id != user.id and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    event_emitter = get_event_emitter(
        {
            "user_id": user.id,
            "chat_id": id,
            "message_id": message_id,
        }
    )

    try:
        if event_emitter:
            await event_emitter(form_data.model_dump())
        else:
            return False
        return True
    except:
        return False


############################
# DeleteChatById
############################


@router.delete("/{id}", response_model=bool)
async def delete_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    if user.role == "admin":
        chat = Chats.get_chat_by_id(id)
        for tag in chat.meta.get("tags", []):
            if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 1:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        result = Chats.delete_chat_by_id(id)

        return result
    else:
        if not has_permission(
            user.id, "chat.delete", request.app.state.config.USER_PERMISSIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

        chat = Chats.get_chat_by_id(id)
        for tag in chat.meta.get("tags", []):
            if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 1:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        result = Chats.delete_chat_by_id_and_user_id(id, user.id)
        return result


############################
# GetPinnedStatusById
############################


@router.get("/{id}/pinned", response_model=Optional[bool])
async def get_pinned_status_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        return chat.pinned
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# PinChatById
############################


@router.post("/{id}/pin", response_model=Optional[ChatResponse])
async def pin_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_pinned_by_id(id)
        return chat
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# CloneChat
############################


class CloneForm(BaseModel):
    title: Optional[str] = None


@router.post("/{id}/clone", response_model=Optional[ChatResponse])
async def clone_chat_by_id(
    form_data: CloneForm, id: str, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        updated_chat = {
            **chat.chat,
            "originalChatId": chat.id,
            "branchPointMessageId": chat.chat["history"]["currentId"],
            "title": form_data.title if form_data.title else f"Clone of {chat.title}",
        }

        chat = Chats.insert_new_chat(user.id, ChatForm(**{"chat": updated_chat}))
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# CloneSharedChatById
############################


@router.post("/{id}/clone/shared", response_model=Optional[ChatResponse])
async def clone_shared_chat_by_id(id: str, user=Depends(get_verified_user)):

    chat = None if user.role == "pending" else _authorized_shared_snapshot(id, user)

    if chat:
        updated_chat = {
            **chat.chat,
            "originalChatId": chat.id,
            "branchPointMessageId": chat.chat["history"]["currentId"],
            "title": f"Clone of {chat.title}",
        }

        chat = Chats.insert_new_chat(user.id, ChatForm(**{"chat": updated_chat}))
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ArchiveChat
############################


@router.post("/{id}/archive", response_model=Optional[ChatResponse])
async def archive_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.toggle_chat_archive_by_id(id)

        # Delete tags if chat is archived
        if chat.archived:
            for tag_id in chat.meta.get("tags", []):
                if Chats.count_chats_by_tag_name_and_user_id(tag_id, user.id) == 0:
                    log.debug(f"deleting tag: {tag_id}")
                    Tags.delete_tag_by_name_and_user_id(tag_id, user.id)
        else:
            for tag_id in chat.meta.get("tags", []):
                tag = Tags.get_tag_by_name_and_user_id(tag_id, user.id)
                if tag is None:
                    log.debug(f"inserting tag: {tag_id}")
                    tag = Tags.insert_new_tag(tag_id, user.id)

        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# ShareChatById
############################


@router.post("/{id}/share", response_model=Optional[ChatResponse])
async def share_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        if chat.share_id:
            shared_chat = Chats.update_shared_chat_by_chat_id(chat.id)
            return _saved_chat_response(
                Chats.get_chat_by_id_and_user_id(id, user.id), user
            )

        shared_chat = Chats.insert_shared_chat_by_chat_id(chat.id)
        if not shared_chat:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT(),
            )
        return _saved_chat_response(Chats.get_chat_by_id_and_user_id(id, user.id), user)

    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


############################
# DeletedSharedChatById
############################


@router.delete("/{id}/share", response_model=Optional[bool])
async def delete_shared_chat_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat is None:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)
    return Chats.revoke_shared_chat(id, user.id)


############################
# UpdateChatFolderIdById
############################


class ChatFolderIdForm(BaseModel):
    folder_id: Optional[str] = None


@router.post("/{id}/folder", response_model=Optional[ChatResponse])
async def update_chat_folder_id_by_id(
    id: str, form_data: ChatFolderIdForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        chat = Chats.update_chat_folder_id_by_id_and_user_id(
            id, user.id, form_data.folder_id
        )
        return ChatResponse(**chat.model_dump())
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetChatTagsById
############################


@router.get("/{id}/tags", response_model=list[TagModel])
async def get_chat_tags_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# AddChatTagById
############################


@router.post("/{id}/tags", response_model=list[TagModel])
async def add_tag_by_id_and_tag_name(
    id: str, form_data: TagForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        tags = chat.meta.get("tags", [])
        tag_id = form_data.name.replace(" ", "_").lower()

        if tag_id == "none":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Tag name cannot be 'None'"),
            )

        if tag_id not in tags:
            Chats.add_chat_tag_by_id_and_user_id_and_tag_name(
                id, user.id, form_data.name
            )

        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# DeleteChatTagById
############################


@router.delete("/{id}/tags", response_model=list[TagModel])
async def delete_tag_by_id_and_tag_name(
    id: str, form_data: TagForm, user=Depends(get_verified_user)
):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        Chats.delete_tag_by_id_and_user_id_and_tag_name(id, user.id, form_data.name)

        if Chats.count_chats_by_tag_name_and_user_id(form_data.name, user.id) == 0:
            Tags.delete_tag_by_name_and_user_id(form_data.name, user.id)

        chat = Chats.get_chat_by_id_and_user_id(id, user.id)
        tags = chat.meta.get("tags", [])
        return Tags.get_tags_by_ids_and_user_id(tags, user.id)
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )


############################
# DeleteAllTagsById
############################


@router.delete("/{id}/tags/all", response_model=Optional[bool])
async def delete_all_tags_by_id(id: str, user=Depends(get_verified_user)):
    chat = Chats.get_chat_by_id_and_user_id(id, user.id)
    if chat:
        Chats.delete_all_tags_by_id_and_user_id(id, user.id)

        for tag in chat.meta.get("tags", []):
            if Chats.count_chats_by_tag_name_and_user_id(tag, user.id) == 0:
                Tags.delete_tag_by_name_and_user_id(tag, user.id)

        return True
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ERROR_MESSAGES.NOT_FOUND
        )
