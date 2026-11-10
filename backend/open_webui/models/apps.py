# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Persistent Apps and channel membership state."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Annotated, Any, Literal, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)
from sqlalchemy import BigInteger, Boolean, Column, String, Text

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import Base, JSONField, get_db


OpenMapping = Annotated[
    dict[str, Any], WithJsonSchema({"type": "object", "additionalProperties": True})
]
AppScope = Literal[
    "channels:mentions:read",
    "channels:messages:read",
    "channels:messages:write",
    "channels:read",
]
AppSecret = Annotated[
    SecretStr,
    StringConstraints(min_length=1, max_length=4096),
]


def _cipher() -> Fernet:
    derived = hashlib.sha256(("openatelier-apps:" + WEBUI_SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _encrypt(value: Optional[str]) -> Optional[str]:
    return _cipher().encrypt(value.encode()).decode() if value is not None else None


def _decrypt(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return _cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
        return None


class Manifest(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    profile_image_url: Optional[str] = Field(default=None, max_length=196608)
    public_key: str = Field(max_length=2000)
    requested_scopes: list[AppScope]

    model_config = ConfigDict(extra="forbid")

    @field_validator("public_key")
    @classmethod
    def ed25519_public_key(cls, value: str) -> str:
        try:
            key = serialization.load_pem_public_key(value.encode())
        except (ValueError, TypeError) as error:
            raise ValueError("Unable to load PEM public key") from error
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("An Ed25519 public key is required")
        return key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()


class AppForm(BaseModel):
    manifest: Manifest
    url: str = Field(max_length=2048)
    api_key: AppSecret
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    granted_scopes: list[AppScope]
    access_grants: list[OpenMapping] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class AppManifestForm(BaseModel):
    url: str = Field(max_length=2048)
    api_key: Optional[AppSecret] = None
    app_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class AppUpdateForm(BaseModel):
    is_active: Optional[bool] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    settings: Optional[OpenMapping] = None
    manifest: Optional[Manifest] = None
    granted_scopes: Optional[list[AppScope]] = None
    access_grants: Optional[list[OpenMapping]] = None

    model_config = ConfigDict(extra="forbid")


class AppConnectionForm(BaseModel):
    api_key: Optional[AppSecret] = None

    model_config = ConfigDict(extra="forbid")


class AppInteractionForm(BaseModel):
    message_id: str
    action_id: str = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class AppInvitation(BaseModel):
    app_id: str

    model_config = ConfigDict(extra="forbid")


class AppResponse(BaseModel):
    connection_status: Literal["connecting", "connected", "disconnected"] = "disconnected"
    id: str
    slug: str
    name: str
    is_active: bool
    configured: bool
    data: OpenMapping
    created_at: int
    updated_at: int


class AppAccessResponse(AppResponse):
    access_grants: list[OpenMapping] = Field(default_factory=list)


class App(Base):
    __tablename__ = "app"

    id = Column(String, primary_key=True)
    slug = Column(String, nullable=False, unique=True, index=True)
    name = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    configured = Column(Boolean, nullable=False, default=True)
    data = Column(JSONField, nullable=False)
    access_grants = Column(JSONField, nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    connection_token_encrypted = Column(Text, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class ChannelApp(Base):
    __tablename__ = "channel_app"

    channel_id = Column(String, primary_key=True)
    app_id = Column(String, primary_key=True)
    created_at = Column(BigInteger, nullable=False)


def _response(row: App, connection_status: str = "disconnected") -> AppResponse:
    return AppResponse(
        connection_status=connection_status,
        id=row.id,
        slug=row.slug,
        name=row.name,
        is_active=bool(row.is_active),
        configured=bool(row.configured),
        data=row.data or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AppsTable:
    def create(self, form: AppForm) -> AppResponse:
        now = int(time.time_ns())
        row = App(
            id=str(uuid.uuid4()),
            slug=form.slug,
            name=form.manifest.name,
            is_active=False,
            configured=True,
            data={
                "manifest": form.manifest.model_dump(),
                "url": form.url,
                "granted_scopes": form.granted_scopes,
                "settings": {},
            },
            access_grants=form.access_grants,
            api_key_encrypted=_encrypt(form.api_key.get_secret_value()),
            connection_token_encrypted=None,
            created_at=now,
            updated_at=now,
        )
        with get_db() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return _response(row)

    def list(self) -> list[AppResponse]:
        with get_db() as db:
            rows = db.query(App).order_by(App.created_at.asc()).all()
            return [_response(row) for row in rows]

    def get(self, app_id: str) -> Optional[AppResponse]:
        with get_db() as db:
            row = db.get(App, app_id)
            return _response(row) if row else None

    def access(self, app_id: str) -> Optional[AppAccessResponse]:
        with get_db() as db:
            row = db.get(App, app_id)
            if row is None:
                return None
            return AppAccessResponse(
                **_response(row).model_dump(),
                access_grants=row.access_grants or [],
            )

    def credentials(self, app_id: str) -> tuple[Optional[str], Optional[str]]:
        with get_db() as db:
            row = db.get(App, app_id)
            if row is None:
                return None, None
            return _decrypt(row.api_key_encrypted), _decrypt(row.connection_token_encrypted)

    def set_connected(
        self,
        app_id: str,
        api_key: str,
        connection_token: str,
        installation_id: str,
    ) -> Optional[AppResponse]:
        with get_db() as db:
            row = db.get(App, app_id)
            if row is None:
                return None
            data = dict(row.data or {})
            data["installation_id"] = installation_id
            row.data = data
            row.api_key_encrypted = _encrypt(api_key)
            row.connection_token_encrypted = _encrypt(connection_token)
            row.is_active = True
            row.configured = True
            row.updated_at = int(time.time_ns())
            db.commit()
            db.refresh(row)
            return _response(row)

    def update(self, app_id: str, form: AppUpdateForm) -> Optional[AppResponse]:
        with get_db() as db:
            row = db.get(App, app_id)
            if row is None:
                return None
            data = dict(row.data or {})
            if form.name is not None:
                row.name = form.name
            if form.settings is not None:
                data["settings"] = form.settings
            if form.manifest is not None:
                data["manifest"] = form.manifest.model_dump()
            if form.granted_scopes is not None:
                data["granted_scopes"] = form.granted_scopes
            if form.access_grants is not None:
                row.access_grants = form.access_grants
            if form.is_active is not None:
                row.is_active = form.is_active
            row.data = data
            row.updated_at = int(time.time_ns())
            db.commit()
            db.refresh(row)
            return _response(row)

    def uninstall(self, app_id: str) -> Optional[AppResponse]:
        with get_db() as db:
            row = db.get(App, app_id)
            if row is None:
                return None
            db.query(ChannelApp).filter_by(app_id=app_id).delete()
            row.is_active = False
            row.configured = False
            row.api_key_encrypted = None
            row.connection_token_encrypted = None
            data = dict(row.data or {})
            data.pop("installation_id", None)
            row.data = data
            row.updated_at = int(time.time_ns())
            db.commit()
            db.refresh(row)
            return _response(row)

    def add_to_channel(self, channel_id: str, app_id: str) -> None:
        with get_db() as db:
            if db.get(ChannelApp, (channel_id, app_id)) is None:
                db.add(ChannelApp(channel_id=channel_id, app_id=app_id, created_at=int(time.time_ns())))
                db.commit()

    def remove_from_channel(self, channel_id: str, app_id: str) -> None:
        with get_db() as db:
            db.query(ChannelApp).filter_by(channel_id=channel_id, app_id=app_id).delete()
            db.commit()

    def channel_apps(self, channel_id: str) -> list[AppResponse]:
        with get_db() as db:
            rows = (
                db.query(App)
                .join(ChannelApp, ChannelApp.app_id == App.id)
                .filter(ChannelApp.channel_id == channel_id)
                .order_by(ChannelApp.created_at.asc())
                .all()
            )
            return [_response(row) for row in rows]


Apps = AppsTable()
