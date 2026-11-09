import json
import secrets
import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, engine, get_db
from open_webui.utils.access_control import has_access
from open_webui.utils.access_grants import convert_explicit_access_grants

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import BigInteger, Boolean, Column, String, Text, JSON, UniqueConstraint
from sqlalchemy import or_, func, select, and_, text
from sqlalchemy.sql import exists

####################
# Channel DB Schema
####################


class Channel(Base):
    __tablename__ = "channel"

    id = Column(Text, primary_key=True)
    user_id = Column(Text)
    type = Column(Text, nullable=True)

    name = Column(Text)
    description = Column(Text, nullable=True)

    data = Column(JSON, nullable=True)
    meta = Column(JSON, nullable=True)
    access_control = Column(JSON, nullable=True)

    created_at = Column(BigInteger)
    updated_at = Column(BigInteger)


class ChannelModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    type: Optional[str] = None

    name: str
    description: Optional[str] = None

    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_control: Optional[dict] = None

    created_at: int  # timestamp in epoch
    updated_at: int  # timestamp in epoch


####################
# Forms
####################


class ChannelForm(BaseModel):
    name: str
    description: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_control: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def convert_current_channel(cls, data):
        if not isinstance(data, dict):
            return data
        if "is_private" in data:
            converted = dict(data)
            is_private = converted.pop("is_private")
            if not isinstance(is_private, bool):
                raise ValueError("is_private must be a boolean")
            converted.pop("user_ids", None)
            converted.pop("access_grants", None)
            converted["access_control"] = {} if is_private else None
            return converted
        if "access_grants" in data:
            return convert_explicit_access_grants(data)
        return data


class ChannelTable:
    def insert_new_channel(
        self, type: Optional[str], form_data: ChannelForm, user_id: str
    ) -> Optional[ChannelModel]:
        with get_db() as db:
            channel = ChannelModel(
                **{
                    **form_data.model_dump(),
                    "type": type,
                    "name": form_data.name.lower(),
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "created_at": int(time.time_ns()),
                    "updated_at": int(time.time_ns()),
                }
            )

            new_channel = Channel(**channel.model_dump())

            db.add(new_channel)
            db.commit()
            return channel

    def get_channels(self) -> list[ChannelModel]:
        with get_db() as db:
            channels = db.query(Channel).all()
            return [ChannelModel.model_validate(channel) for channel in channels]

    def get_channels_by_user_id(
        self, user_id: str, permission: str = "read"
    ) -> list[ChannelModel]:
        channels = self.get_channels()
        return [
            channel
            for channel in channels
            if channel.user_id == user_id
            or has_access(user_id, permission, channel.access_control)
        ]

    def get_channel_by_id(self, id: str) -> Optional[ChannelModel]:
        with get_db() as db:
            channel = db.query(Channel).filter(Channel.id == id).first()
            return ChannelModel.model_validate(channel) if channel else None

    def update_channel_by_id(
        self, id: str, form_data: ChannelForm
    ) -> Optional[ChannelModel]:
        with get_db() as db:
            channel = db.query(Channel).filter(Channel.id == id).first()
            if not channel:
                return None

            channel.name = form_data.name
            channel.data = form_data.data
            channel.meta = form_data.meta
            channel.access_control = form_data.access_control
            channel.updated_at = int(time.time_ns())

            db.commit()
            return ChannelModel.model_validate(channel) if channel else None

    def delete_channel_by_id(self, id: str):
        with get_db() as db:
            db.query(Channel).filter(Channel.id == id).delete()
            db.commit()
            return True


Channels = ChannelTable()


class OpenAtelierChannelMember(Base):
    __tablename__ = "openatelier_channel_member"
    __table_args__ = (
        UniqueConstraint("channel_id", "user_id", name="uq_openatelier_channel_member"),
    )

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False, index=True)
    user_id = Column(Text, nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(BigInteger, nullable=False)


class OpenAtelierChannelWebhook(Base):
    __tablename__ = "openatelier_channel_webhook"

    id = Column(Text, primary_key=True)
    channel_id = Column(Text, nullable=False, index=True)
    user_id = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    profile_image_url = Column(Text, nullable=True)
    token = Column(Text, nullable=False, unique=True)
    last_used_at = Column(BigInteger, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class OpenAtelierChannelMessageState(Base):
    __tablename__ = "openatelier_channel_message_state"

    message_id = Column(Text, primary_key=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    pinned_at = Column(BigInteger, nullable=True)
    pinned_by = Column(Text, nullable=True)


class OpenAtelierChannelPrivacy(Base):
    __tablename__ = "openatelier_channel_privacy"

    channel_id = Column(Text, primary_key=True)
    is_private = Column(Boolean, nullable=True)


class ChannelCompatTable:
    """Additive persistence for measured post-0.6 Channel behavior."""

    def __init__(self):
        for table in (
            OpenAtelierChannelMember.__table__,
            OpenAtelierChannelWebhook.__table__,
            OpenAtelierChannelMessageState.__table__,
            OpenAtelierChannelPrivacy.__table__,
        ):
            table.create(bind=engine, checkfirst=True)

    def add_members(self, channel_id: str, user_ids: list[str]) -> list[str]:
        added = []
        with get_db() as db:
            for user_id in dict.fromkeys(user_ids):
                exists = (
                    db.query(OpenAtelierChannelMember)
                    .filter_by(channel_id=channel_id, user_id=user_id)
                    .first()
                )
                if exists:
                    continue
                db.add(
                    OpenAtelierChannelMember(
                        id=str(uuid.uuid4()),
                        channel_id=channel_id,
                        user_id=user_id,
                        is_active=True,
                        created_at=int(time.time_ns()),
                    )
                )
                added.append(user_id)
            db.commit()
        return added

    def remove_members(self, channel_id: str, user_ids: list[str]) -> int:
        with get_db() as db:
            count = (
                db.query(OpenAtelierChannelMember)
                .filter(
                    OpenAtelierChannelMember.channel_id == channel_id,
                    OpenAtelierChannelMember.user_id.in_(list(dict.fromkeys(user_ids))),
                )
                .delete(synchronize_session=False)
            )
            db.commit()
            return count

    def member_ids(self, channel_id: str) -> list[str]:
        with get_db() as db:
            rows = (
                db.query(OpenAtelierChannelMember)
                .filter_by(channel_id=channel_id)
                .order_by(OpenAtelierChannelMember.created_at.asc())
                .all()
            )
            return [row.user_id for row in rows]

    def member_records(self, channel_id: str, user_ids: list[str]) -> list[dict]:
        with get_db() as db:
            rows = (
                db.query(OpenAtelierChannelMember)
                .filter(
                    OpenAtelierChannelMember.channel_id == channel_id,
                    OpenAtelierChannelMember.user_id.in_(user_ids),
                )
                .order_by(OpenAtelierChannelMember.created_at.asc())
                .all()
            )
            return [
                {
                    "id": row.id,
                    "channel_id": row.channel_id,
                    "user_id": row.user_id,
                    "is_active": row.is_active,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    def channel_ids(self, user_id: str) -> list[str]:
        with get_db() as db:
            rows = (
                db.query(OpenAtelierChannelMember)
                .filter_by(user_id=user_id)
                .order_by(OpenAtelierChannelMember.created_at.asc())
                .all()
            )
            return [row.channel_id for row in rows]

    def is_member(self, channel_id: str, user_id: str, active_only: bool = False) -> bool:
        with get_db() as db:
            query = db.query(OpenAtelierChannelMember).filter_by(
                channel_id=channel_id, user_id=user_id
            )
            if active_only:
                query = query.filter_by(is_active=True)
            return query.first() is not None

    def set_member_active(self, channel_id: str, user_id: str, value: bool) -> bool:
        with get_db() as db:
            row = (
                db.query(OpenAtelierChannelMember)
                .filter_by(channel_id=channel_id, user_id=user_id, is_active=True)
                .first()
            )
            if row is None:
                return False
            row.is_active = value
            db.commit()
            return True

    def find_dm(self, first_user_id: str, second_user_id: str):
        first = set(self.channel_ids(first_user_id))
        second = set(self.channel_ids(second_user_id))
        for channel_id in first & second:
            channel = Channels.get_channel_by_id(channel_id)
            if channel and channel.type == "dm":
                return channel
        return None

    def set_privacy(self, channel_id: str, is_private: Optional[bool]):
        with get_db() as db:
            row = db.get(OpenAtelierChannelPrivacy, channel_id)
            if row is None:
                db.add(OpenAtelierChannelPrivacy(channel_id=channel_id, is_private=is_private))
            else:
                row.is_private = is_private
            db.commit()

    def get_privacy(self, channel_id: str) -> Optional[bool]:
        with get_db() as db:
            row = db.get(OpenAtelierChannelPrivacy, channel_id)
            return row.is_private if row else None

    def set_pin(self, message_id: str, is_pinned: bool, user_id: str):
        with get_db() as db:
            row = db.get(OpenAtelierChannelMessageState, message_id)
            if row is None:
                row = OpenAtelierChannelMessageState(message_id=message_id)
                db.add(row)
            row.is_pinned = is_pinned
            row.pinned_at = int(time.time_ns()) if is_pinned else None
            row.pinned_by = user_id if is_pinned else None
            db.commit()

    def pin(self, message_id: str) -> dict:
        with get_db() as db:
            row = db.get(OpenAtelierChannelMessageState, message_id)
            return {
                "is_pinned": bool(row and row.is_pinned),
                "pinned_at": row.pinned_at if row else None,
                "pinned_by": row.pinned_by if row else None,
            }

    def create_webhook(self, channel_id: str, user_id: str, name: str, profile_image_url=None):
        now = int(time.time_ns())
        row = OpenAtelierChannelWebhook(
            id=str(uuid.uuid4()),
            channel_id=channel_id,
            user_id=user_id,
            name=name,
            profile_image_url=profile_image_url,
            token=secrets.token_urlsafe(32),
            last_used_at=None,
            created_at=now,
            updated_at=now,
        )
        with get_db() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return self.webhook_dict(row)

    @staticmethod
    def webhook_dict(row):
        return {
            "id": row.id,
            "channel_id": row.channel_id,
            "user_id": row.user_id,
            "name": row.name,
            "profile_image_url": row.profile_image_url,
            "token": row.token,
            "last_used_at": row.last_used_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def get_webhook(self, webhook_id: str):
        with get_db() as db:
            row = db.get(OpenAtelierChannelWebhook, webhook_id)
            return self.webhook_dict(row) if row else None

    def get_webhook_by_token(self, webhook_id: str, token: str):
        with get_db() as db:
            row = (
                db.query(OpenAtelierChannelWebhook)
                .filter_by(id=webhook_id, token=token)
                .first()
            )
            return self.webhook_dict(row) if row else None

    def list_webhooks(self, channel_id: str):
        with get_db() as db:
            rows = (
                db.query(OpenAtelierChannelWebhook)
                .filter_by(channel_id=channel_id)
                .order_by(OpenAtelierChannelWebhook.created_at.asc())
                .all()
            )
            return [self.webhook_dict(row) for row in rows]

    def update_webhook(self, webhook_id: str, name: str, profile_image_url=None):
        with get_db() as db:
            row = db.get(OpenAtelierChannelWebhook, webhook_id)
            if row is None:
                return None
            row.name = name
            row.profile_image_url = profile_image_url
            row.updated_at = int(time.time_ns())
            db.commit()
            db.refresh(row)
            return self.webhook_dict(row)

    def touch_webhook(self, webhook_id: str):
        with get_db() as db:
            row = db.get(OpenAtelierChannelWebhook, webhook_id)
            if row:
                row.last_used_at = int(time.time_ns())
                db.commit()

    def delete_webhook(self, webhook_id: str) -> bool:
        with get_db() as db:
            count = db.query(OpenAtelierChannelWebhook).filter_by(id=webhook_id).delete()
            db.commit()
            return bool(count)

    def delete_channel_state(self, channel_id: str) -> None:
        with get_db() as db:
            db.query(OpenAtelierChannelMember).filter_by(channel_id=channel_id).delete()
            db.query(OpenAtelierChannelWebhook).filter_by(channel_id=channel_id).delete()
            db.query(OpenAtelierChannelPrivacy).filter_by(channel_id=channel_id).delete()
            db.commit()


ChannelCompat = ChannelCompatTable()
