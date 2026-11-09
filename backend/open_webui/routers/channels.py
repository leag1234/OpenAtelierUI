import json
import logging
from typing import Any, Optional


from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field


from open_webui.socket.main import sio, get_user_ids_from_room
from open_webui.models.users import Users, UserNameResponse

from open_webui.models.channels import Channels, ChannelCompat, ChannelModel, ChannelForm
from open_webui.models.messages import (
    Messages,
    MessageModel,
    MessageResponse,
    MessageForm,
)


from open_webui.config import ENABLE_ADMIN_CHAT_ACCESS, ENABLE_ADMIN_EXPORT
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS


from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access, get_users_with_access
from open_webui.utils.webhook import post_webhook

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MODELS"])

router = APIRouter()


class CreateChannelForm(BaseModel):
    name: str = ""
    description: Optional[str] = None
    type: Optional[str] = None
    is_private: Optional[bool] = None
    user_ids: Optional[list[str]] = None
    group_ids: Optional[list[str]] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: Optional[list[dict]] = None


class ChannelFullResponse(BaseModel):
    id: str
    user_id: str
    type: Optional[str] = None
    name: str
    description: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: list[dict] = Field(default_factory=list)
    is_private: Optional[bool] = None
    archived_at: Optional[int] = None
    archived_by: Optional[str] = None
    deleted_at: Optional[int] = None
    deleted_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: int
    updated_at: int


class UserIdNameStatusResponse(BaseModel):
    id: str
    name: str
    email: str
    role: str
    profile_image_url: str
    is_active: bool = True
    status_message: Optional[str] = None
    status_emoji: Optional[str] = None
    status_expires_at: Optional[int] = None
    presence_state: Optional[str] = None


class ChannelMemberListResponse(BaseModel):
    users: list[UserIdNameStatusResponse]
    total: int


class UpdateMembersForm(BaseModel):
    user_ids: list[str] = Field(default_factory=list)
    group_ids: list[str] = Field(default_factory=list)


class RemoveMembersForm(BaseModel):
    user_ids: list[str] = Field(default_factory=list)


class UpdateActiveMemberForm(BaseModel):
    is_active: bool


class PinMessageForm(BaseModel):
    is_pinned: bool


class ChannelWebhookForm(BaseModel):
    name: str
    profile_image_url: Optional[str] = None


class ChannelWebhookModel(BaseModel):
    id: str
    channel_id: str
    user_id: str
    name: str
    profile_image_url: Optional[str] = None
    token: str
    last_used_at: Optional[int] = None
    created_at: int
    updated_at: int


class WebhookMessageForm(BaseModel):
    content: str


def _channel_payload(channel):
    payload = channel.model_dump()
    payload.pop("access_control", None)
    payload.update(
        {
            "access_grants": [],
            "is_private": ChannelCompat.get_privacy(channel.id),
            "archived_at": None,
            "archived_by": None,
            "deleted_at": None,
            "deleted_by": None,
            "updated_by": None,
        }
    )
    return payload


def _can_read_channel(user, channel):
    return (
        user.role == "admin"
        or channel.user_id == user.id
        or ChannelCompat.is_member(channel.id, user.id)
        or has_access(user.id, type="read", access_control=channel.access_control)
    )


def _require_channel(channel_id: str):
    channel = Channels.get_channel_by_id(channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return channel


def _require_channel_read(channel_id: str, user):
    channel = _require_channel(channel_id)
    if not _can_read_channel(user, channel):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT())
    return channel


def _require_channel_admin(channel_id: str, user):
    channel = _require_channel(channel_id)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.UNAUTHORIZED)
    return channel


def _user_status(user_id: str):
    member = Users.get_user_by_id(user_id)
    if member is None:
        return None
    return {
        "id": member.id,
        "name": member.name,
        "email": member.email,
        "role": member.role,
        "profile_image_url": member.profile_image_url,
        "is_active": True,
        "status_message": getattr(member, "status_message", None),
        "status_emoji": getattr(member, "status_emoji", None),
        "status_expires_at": getattr(member, "status_expires_at", None),
        "presence_state": None,
    }


def _member_payload(record: dict, invited_by: str):
    created_at = record["created_at"]
    return {
        "id": record["id"],
        "channel_id": record["channel_id"],
        "user_id": record["user_id"],
        "role": None,
        "status": "joined",
        "is_active": record["is_active"],
        "is_channel_pinned": False,
        "is_channel_muted": False,
        "last_read_at": created_at,
        "invited_by": invited_by,
        "invited_at": created_at,
        "joined_at": created_at,
        "left_at": None,
        "data": None,
        "meta": None,
        "created_at": created_at,
        "updated_at": created_at,
        "app_id": None,
    }

############################
# GetChatList
############################


@router.get("/", response_model=list[ChannelModel])
async def get_channels(user=Depends(get_verified_user)):
    if user.role == "admin":
        return Channels.get_channels()
    else:
        return Channels.get_channels_by_user_id(user.id)


############################
# CreateNewChannel
############################


@router.post("/create", response_model=Optional[ChannelFullResponse])
async def create_new_channel(form_data: CreateChannelForm, user=Depends(get_admin_user)):
    try:
        channel = Channels.insert_new_channel(
            form_data.type,
            ChannelForm(
                name=form_data.name,
                description=form_data.description,
                data=form_data.data,
                meta=form_data.meta,
                access_control={} if form_data.is_private else None,
            ),
            user.id,
        )
        ChannelCompat.set_privacy(channel.id, form_data.is_private)
        ChannelCompat.add_members(channel.id, [user.id, *(form_data.user_ids or [])])
        return _channel_payload(channel)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


@router.get("/list", response_model=list[ChannelFullResponse])
async def get_all_channels(user=Depends(get_verified_user)):
    if user.role == "admin":
        channels = Channels.get_channels()
    else:
        identifiers = list(
            dict.fromkeys(
                [
                    *ChannelCompat.channel_ids(user.id),
                    *(
                        channel.id
                        for channel in Channels.get_channels_by_user_id(user.id)
                    ),
                ]
            )
        )
        channels = [
            channel
            for identifier in identifiers
            if (channel := Channels.get_channel_by_id(identifier)) is not None
        ]
    return [_channel_payload(channel) for channel in channels]


@router.get("/users/{user_id}", response_model=Optional[ChannelFullResponse])
async def get_dm_channel_by_user_id(user_id: str, user=Depends(get_verified_user)):
    target = Users.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    channel = ChannelCompat.find_dm(user.id, user_id)
    if channel is None:
        channel = Channels.insert_new_channel(
            "dm", ChannelForm(name="", access_control={}), user.id
        )
        ChannelCompat.set_privacy(channel.id, None)
        ChannelCompat.add_members(channel.id, [user.id, user_id])
    return _channel_payload(channel)


@router.get("/webhooks/{webhook_id}/profile/image")
async def get_channel_webhook_profile_image(
    webhook_id: str, user=Depends(get_verified_user)
):
    webhook = ChannelCompat.get_webhook(webhook_id)
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    return RedirectResponse(webhook.get("profile_image_url") or "/user.png")


@router.post("/webhooks/{webhook_id}/{token}")
async def post_channel_webhook_message(
    webhook_id: str, token: str, form_data: WebhookMessageForm
):
    webhook = ChannelCompat.get_webhook_by_token(webhook_id, token)
    if webhook is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The URL you provided is invalid. Please double-check and try again.",
        )
    message = Messages.insert_new_message(
        MessageForm(
            content=form_data.content,
            meta={"webhook": {"id": webhook_id}},
        ),
        webhook["channel_id"],
        webhook["user_id"],
    )
    ChannelCompat.touch_webhook(webhook_id)
    return {"success": True, "message_id": message.id}


############################
# GetChannelById
############################


@router.get("/{id}", response_model=Optional[ChannelModel])
async def get_channel_by_id(id: str, user=Depends(get_verified_user)):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    return ChannelModel(**channel.model_dump())


############################
# UpdateChannelById
############################


@router.post("/{id}/update", response_model=Optional[ChannelModel])
async def update_channel_by_id(
    id: str, form_data: ChannelForm, user=Depends(get_admin_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    try:
        channel = Channels.update_channel_by_id(id, form_data)
        return ChannelModel(**channel.model_dump())
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# DeleteChannelById
############################


@router.delete("/{id}/delete", response_model=bool)
async def delete_channel_by_id(id: str, user=Depends(get_admin_user)):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    try:
        ChannelCompat.delete_channel_state(id)
        Channels.delete_channel_by_id(id)
        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


@router.get("/{id}/members", response_model=ChannelMemberListResponse)
async def get_channel_members_by_id(
    id: str,
    query: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_verified_user),
):
    _require_channel_read(id, user)
    members = [
        member
        for user_id in ChannelCompat.member_ids(id)
        if (member := _user_status(user_id)) is not None
    ]
    if query:
        needle = query.casefold()
        members = [
            member
            for member in members
            if needle in member["name"].casefold()
            or needle in member["email"].casefold()
        ]
    reverse = direction == "desc"
    if order_by in {"name", "email", "role"}:
        members.sort(key=lambda member: member[order_by].casefold(), reverse=reverse)
    return {"users": members, "total": len(members)}


@router.post("/{id}/members/active", response_model=bool)
async def update_is_active_member_by_id_and_user_id(
    id: str,
    form_data: UpdateActiveMemberForm,
    user=Depends(get_verified_user),
):
    _require_channel(id)
    if not ChannelCompat.set_member_active(id, user.id, form_data.is_active):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    return True


@router.post("/{id}/update/members/add")
async def add_members_by_id(
    id: str, form_data: UpdateMembersForm, user=Depends(get_verified_user)
):
    _require_channel(id)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )
    added = ChannelCompat.add_members(id, form_data.user_ids)
    return [
        _member_payload(record, user.id)
        for record in ChannelCompat.member_records(id, added)
    ]


@router.post("/{id}/update/members/remove")
async def remove_members_by_id(
    id: str, form_data: RemoveMembersForm, user=Depends(get_verified_user)
):
    _require_channel(id)
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )
    return ChannelCompat.remove_members(id, form_data.user_ids)


@router.get("/{id}/webhooks", response_model=list[ChannelWebhookModel])
async def get_channel_webhooks(id: str, user=Depends(get_verified_user)):
    _require_channel_admin(id, user)
    return ChannelCompat.list_webhooks(id)


@router.post("/{id}/webhooks/create", response_model=ChannelWebhookModel)
async def create_channel_webhook(
    id: str, form_data: ChannelWebhookForm, user=Depends(get_verified_user)
):
    _require_channel_admin(id, user)
    return ChannelCompat.create_webhook(
        id, user.id, form_data.name, form_data.profile_image_url
    )


def _channel_webhook(id: str, webhook_id: str):
    webhook = ChannelCompat.get_webhook(webhook_id)
    if webhook is None or webhook["channel_id"] != id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    return webhook


@router.post(
    "/{id}/webhooks/{webhook_id}/update", response_model=ChannelWebhookModel
)
async def update_channel_webhook(
    id: str,
    webhook_id: str,
    form_data: ChannelWebhookForm,
    user=Depends(get_verified_user),
):
    _require_channel_admin(id, user)
    _channel_webhook(id, webhook_id)
    return ChannelCompat.update_webhook(
        webhook_id, form_data.name, form_data.profile_image_url
    )


@router.delete("/{id}/webhooks/{webhook_id}/delete", response_model=bool)
async def delete_channel_webhook(
    id: str, webhook_id: str, user=Depends(get_verified_user)
):
    _require_channel_admin(id, user)
    _channel_webhook(id, webhook_id)
    return ChannelCompat.delete_webhook(webhook_id)


############################
# GetChannelMessages
############################


class ChannelMessageUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    role: str


class MessageUserResponse(MessageResponse):
    data: Optional[dict | bool] = None
    user: ChannelMessageUser
    is_pinned: bool = False
    pinned_at: Optional[int] = None
    pinned_by: Optional[str] = None
    reply_to_id: Optional[str] = None
    reply_to_message: Optional[dict] = None
    app_id: Optional[str] = None
    etag: Optional[str] = None


class PinnedMessageResponse(BaseModel):
    id: str
    user_id: str
    channel_id: Optional[str] = None
    parent_id: Optional[str] = None
    content: str
    data: bool
    meta: Optional[dict] = None
    created_at: int
    updated_at: int
    reactions: list
    user: ChannelMessageUser
    is_pinned: bool
    pinned_at: Optional[int] = None
    pinned_by: Optional[str] = None
    reply_to_id: Optional[str] = None
    app_id: Optional[str] = None
    etag: Optional[str] = None


class MessagePostResponse(MessageModel):
    is_pinned: bool = False
    pinned_at: Optional[int] = None
    pinned_by: Optional[str] = None
    reply_to_id: Optional[str] = None
    app_id: Optional[str] = None
    etag: Optional[str] = None


def _message_user_response(message):
    replies = Messages.get_replies_by_message_id(message.id)
    actor = Users.get_user_by_id(message.user_id)
    webhook_id = ((message.meta or {}).get("webhook") or {}).get("id")
    webhook = ChannelCompat.get_webhook(webhook_id) if webhook_id else None
    if webhook:
        actor_payload = {
            "id": webhook["id"],
            "name": webhook["name"],
            "role": "webhook",
        }
    elif actor:
        actor_payload = {
            "id": actor.id,
            "name": actor.name,
            "role": actor.role,
        }
    else:
        actor_payload = {
            "id": message.user_id,
            "name": "Deleted User",
            "role": "user",
        }
    return MessageUserResponse(
        **{
            **message.model_dump(),
            "data": bool(message.data),
            "reply_count": len(replies),
            "latest_reply_at": replies[0].created_at if replies else None,
            "reactions": Messages.get_reactions_by_message_id(message.id),
            "user": actor_payload,
            **ChannelCompat.pin(message.id),
            "reply_to_id": None,
            "reply_to_message": None,
            "app_id": None,
            "etag": str(message.updated_at),
        }
    )


@router.get("/{id}/messages", response_model=list[MessageUserResponse])
async def get_channel_messages(
    id: str, skip: int = 0, limit: int = 50, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message_list = Messages.get_messages_by_channel_id(id, skip, limit)
    return [_message_user_response(message) for message in message_list]


@router.get("/{id}/messages/pinned", response_model=list[PinnedMessageResponse])
async def get_pinned_channel_messages(
    id: str, page: int = 1, user=Depends(get_verified_user)
):
    _require_channel_read(id, user)
    messages = Messages.get_messages_by_channel_id(id, max(page - 1, 0) * 50, 50)
    return [
        PinnedMessageResponse(
            **_message_user_response(message).model_dump(
                exclude={"latest_reply_at", "reply_count", "reply_to_message"}
            )
        )
        for message in messages
        if ChannelCompat.pin(message.id)["is_pinned"]
    ]


############################
# PostNewMessage
############################


async def send_notification(name, webui_url, channel, message, active_user_ids):
    users = get_users_with_access("read", channel.access_control)

    for user in users:
        if user.id in active_user_ids:
            continue
        else:
            if user.settings:
                webhook_url = user.settings.ui.get("notifications", {}).get(
                    "webhook_url", None
                )

                if webhook_url:
                    post_webhook(
                        name,
                        webhook_url,
                        f"#{channel.name} - {webui_url}/channels/{channel.id}\n\n{message.content}",
                        {
                            "action": "channel",
                            "message": message.content,
                            "title": channel.name,
                            "url": f"{webui_url}/channels/{channel.id}",
                        },
                    )


@router.post("/{id}/messages/post", response_model=Optional[MessagePostResponse])
async def post_new_message(
    request: Request,
    id: str,
    form_data: MessageForm,
    background_tasks: BackgroundTasks,
    user=Depends(get_verified_user),
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    try:
        message = Messages.insert_new_message(form_data, channel.id, user.id)

        if message:
            event_data = {
                "channel_id": channel.id,
                "message_id": message.id,
                "data": {
                    "type": "message",
                    "data": MessageUserResponse(
                        **{
                            **message.model_dump(),
                            "reply_count": 0,
                            "latest_reply_at": None,
                            "reactions": Messages.get_reactions_by_message_id(
                                message.id
                            ),
                            "user": UserNameResponse(**user.model_dump()),
                        }
                    ).model_dump(),
                },
                "user": UserNameResponse(**user.model_dump()).model_dump(),
                "channel": channel.model_dump(),
            }

            await sio.emit(
                "channel-events",
                event_data,
                to=f"channel:{channel.id}",
            )

            if message.parent_id:
                # If this message is a reply, emit to the parent message as well
                parent_message = Messages.get_message_by_id(message.parent_id)

                if parent_message:
                    await sio.emit(
                        "channel-events",
                        {
                            "channel_id": channel.id,
                            "message_id": parent_message.id,
                            "data": {
                                "type": "message:reply",
                                "data": MessageUserResponse(
                                    **{
                                        **parent_message.model_dump(),
                                        "user": UserNameResponse(
                                            **Users.get_user_by_id(
                                                parent_message.user_id
                                            ).model_dump()
                                        ),
                                    }
                                ).model_dump(),
                            },
                            "user": UserNameResponse(**user.model_dump()).model_dump(),
                            "channel": channel.model_dump(),
                        },
                        to=f"channel:{channel.id}",
                    )

            active_user_ids = get_user_ids_from_room(f"channel:{channel.id}")

            background_tasks.add_task(
                send_notification,
                request.app.state.WEBUI_NAME,
                request.app.state.config.WEBUI_URL,
                channel,
                message,
                active_user_ids,
            )

        return MessagePostResponse(
            **message.model_dump(),
            **ChannelCompat.pin(message.id),
            reply_to_id=None,
            app_id=None,
            etag=str(message.updated_at),
        )
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# GetChannelMessage
############################


def _message_in_channel(id: str, message_id: str):
    message = Messages.get_message_by_id(message_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )
    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )
    return message


@router.get("/{id}/messages/{message_id}/data", response_model=Optional[dict])
async def get_channel_message_data(
    id: str, message_id: str, user=Depends(get_verified_user)
):
    _require_channel_read(id, user)
    return _message_in_channel(id, message_id).data


@router.post(
    "/{id}/messages/{message_id}/pin", response_model=Optional[MessageUserResponse]
)
async def pin_channel_message(
    id: str,
    message_id: str,
    form_data: PinMessageForm,
    user=Depends(get_verified_user),
):
    _require_channel_read(id, user)
    message = _message_in_channel(id, message_id)
    ChannelCompat.set_pin(message_id, form_data.is_pinned, user.id)
    return _message_user_response(message)


@router.get("/{id}/messages/{message_id}", response_model=Optional[MessageUserResponse])
async def get_channel_message(
    id: str, message_id: str, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message = Messages.get_message_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )

    return _message_user_response(message)


############################
# GetChannelThreadMessages
############################


@router.get(
    "/{id}/messages/{message_id}/thread", response_model=list[MessageUserResponse]
)
async def get_channel_thread_messages(
    id: str,
    message_id: str,
    skip: int = 0,
    limit: int = 50,
    user=Depends(get_verified_user),
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message_list = Messages.get_messages_by_parent_id(id, message_id, skip, limit)
    users = {}

    messages = []
    for message in message_list:
        if message.user_id not in users:
            user = Users.get_user_by_id(message.user_id)
            users[message.user_id] = user

        messages.append(
            MessageUserResponse(
                **{
                    **message.model_dump(),
                    "reply_count": 0,
                    "latest_reply_at": None,
                    "reactions": Messages.get_reactions_by_message_id(message.id),
                    "user": UserNameResponse(**users[message.user_id].model_dump()),
                }
            )
        )

    return messages


############################
# UpdateMessageById
############################


@router.post(
    "/{id}/messages/{message_id}/update", response_model=Optional[MessageModel]
)
async def update_message_by_id(
    id: str, message_id: str, form_data: MessageForm, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message = Messages.get_message_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )

    try:
        message = Messages.update_message_by_id(message_id, form_data)
        message = Messages.get_message_by_id(message_id)

        if message:
            await sio.emit(
                "channel-events",
                {
                    "channel_id": channel.id,
                    "message_id": message.id,
                    "data": {
                        "type": "message:update",
                        "data": MessageUserResponse(
                            **{
                                **message.model_dump(),
                                "user": UserNameResponse(
                                    **user.model_dump()
                                ).model_dump(),
                            }
                        ).model_dump(),
                    },
                    "user": UserNameResponse(**user.model_dump()).model_dump(),
                    "channel": channel.model_dump(),
                },
                to=f"channel:{channel.id}",
            )

        return MessageModel(**message.model_dump())
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# AddReactionToMessage
############################


class ReactionForm(BaseModel):
    name: str


@router.post("/{id}/messages/{message_id}/reactions/add", response_model=bool)
async def add_reaction_to_message(
    id: str, message_id: str, form_data: ReactionForm, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message = Messages.get_message_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )

    try:
        Messages.add_reaction_to_message(message_id, user.id, form_data.name)
        message = Messages.get_message_by_id(message_id)

        await sio.emit(
            "channel-events",
            {
                "channel_id": channel.id,
                "message_id": message.id,
                "data": {
                    "type": "message:reaction:add",
                    "data": {
                        **message.model_dump(),
                        "user": UserNameResponse(
                            **Users.get_user_by_id(message.user_id).model_dump()
                        ).model_dump(),
                        "name": form_data.name,
                    },
                },
                "user": UserNameResponse(**user.model_dump()).model_dump(),
                "channel": channel.model_dump(),
            },
            to=f"channel:{channel.id}",
        )

        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# RemoveReactionById
############################


@router.post("/{id}/messages/{message_id}/reactions/remove", response_model=bool)
async def remove_reaction_by_id_and_user_id_and_name(
    id: str, message_id: str, form_data: ReactionForm, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message = Messages.get_message_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )

    try:
        Messages.remove_reaction_by_id_and_user_id_and_name(
            message_id, user.id, form_data.name
        )

        message = Messages.get_message_by_id(message_id)

        await sio.emit(
            "channel-events",
            {
                "channel_id": channel.id,
                "message_id": message.id,
                "data": {
                    "type": "message:reaction:remove",
                    "data": {
                        **message.model_dump(),
                        "user": UserNameResponse(
                            **Users.get_user_by_id(message.user_id).model_dump()
                        ).model_dump(),
                        "name": form_data.name,
                    },
                },
                "user": UserNameResponse(**user.model_dump()).model_dump(),
                "channel": channel.model_dump(),
            },
            to=f"channel:{channel.id}",
        )

        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )


############################
# DeleteMessageById
############################


@router.delete("/{id}/messages/{message_id}/delete", response_model=bool)
async def delete_message_by_id(
    id: str, message_id: str, user=Depends(get_verified_user)
):
    channel = Channels.get_channel_by_id(id)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not _can_read_channel(user, channel):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.DEFAULT()
        )

    message = Messages.get_message_by_id(message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if message.channel_id != id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )

    try:
        Messages.delete_message_by_id(message_id)
        await sio.emit(
            "channel-events",
            {
                "channel_id": channel.id,
                "message_id": message.id,
                "data": {
                    "type": "message:delete",
                    "data": {
                        **message.model_dump(),
                        "user": UserNameResponse(**user.model_dump()).model_dump(),
                    },
                },
                "user": UserNameResponse(**user.model_dump()).model_dump(),
                "channel": channel.model_dump(),
            },
            to=f"channel:{channel.id}",
        )

        if message.parent_id:
            # If this message is a reply, emit to the parent message as well
            parent_message = Messages.get_message_by_id(message.parent_id)

            if parent_message:
                await sio.emit(
                    "channel-events",
                    {
                        "channel_id": channel.id,
                        "message_id": parent_message.id,
                        "data": {
                            "type": "message:reply",
                            "data": MessageUserResponse(
                                **{
                                    **parent_message.model_dump(),
                                    "user": UserNameResponse(
                                        **Users.get_user_by_id(
                                            parent_message.user_id
                                        ).model_dump()
                                    ),
                                }
                            ).model_dump(),
                        },
                        "user": UserNameResponse(**user.model_dump()).model_dump(),
                        "channel": channel.model_dump(),
                    },
                    to=f"channel:{channel.id}",
                )

        return True
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=ERROR_MESSAGES.DEFAULT()
        )
