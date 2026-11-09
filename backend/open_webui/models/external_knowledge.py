# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Persistent external-knowledge connection configuration."""

import base64
import hashlib
import json
import time
import uuid
from typing import Annotated, Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, WithJsonSchema
from sqlalchemy import BigInteger, Boolean, Column, String, Text

from open_webui.env import WEBUI_SECRET_KEY
from open_webui.internal.db import Base, JSONField, get_db


OpenMapping = Annotated[
    dict[str, Any],
    WithJsonSchema({"type": "object", "additionalProperties": True}),
]


def _cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(WEBUI_SECRET_KEY.encode()).digest())
    return Fernet(key)


def _encrypt_auth_config(value: Optional[dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return _cipher().encrypt(payload).decode()


def _decrypt_auth_config(value: Optional[str]) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    try:
        decoded = json.loads(_cipher().decrypt(value.encode()))
        return decoded if isinstance(decoded, dict) else None
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return None


class ExternalKnowledgeConnection(Base):
    __tablename__ = "external_knowledge_connection"

    id = Column(String, primary_key=True)
    name = Column(Text, nullable=False)
    provider = Column(String, nullable=False)
    endpoint = Column(Text, nullable=False)
    auth_config_encrypted = Column(Text, nullable=True)
    capabilities = Column(JSONField, nullable=True)
    config = Column(JSONField, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    health = Column(JSONField, nullable=True)
    created_by = Column(String, nullable=False, index=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class ExternalKnowledgeConnectionForm(BaseModel):
    name: str
    provider: str
    endpoint: str
    auth_config: Optional[OpenMapping] = None
    capabilities: Optional[OpenMapping] = None
    config: Optional[OpenMapping] = None
    enabled: bool = True


class ExternalKnowledgeConnectionModel(BaseModel):
    id: str
    name: str
    provider: str
    endpoint: str
    capabilities: Optional[dict[str, Any]] = None
    config: Optional[dict[str, Any]] = None
    enabled: bool
    health: Optional[dict[str, Any]] = None
    created_by: str
    created_at: int
    updated_at: int
    auth_configured: bool = False

    model_config = ConfigDict(from_attributes=True)


class ExternalKnowledgeConnectionListResponse(BaseModel):
    items: list[OpenMapping]
    total: int


def _model(row: ExternalKnowledgeConnection) -> ExternalKnowledgeConnectionModel:
    return ExternalKnowledgeConnectionModel(
        id=row.id,
        name=row.name,
        provider=row.provider,
        endpoint=row.endpoint,
        capabilities=row.capabilities,
        config=row.config,
        enabled=bool(row.enabled),
        health=row.health,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        auth_configured=bool(row.auth_config_encrypted),
    )


class ExternalKnowledgeConnectionsTable:
    def insert(
        self, created_by: str, form: ExternalKnowledgeConnectionForm
    ) -> ExternalKnowledgeConnectionModel:
        now = int(time.time())
        row = ExternalKnowledgeConnection(
            id=str(uuid.uuid4()),
            created_by=created_by,
            created_at=now,
            updated_at=now,
            health=None,
            auth_config_encrypted=_encrypt_auth_config(form.auth_config),
            **form.model_dump(exclude={"auth_config"}),
        )
        with get_db() as db:
            db.add(row)
            db.commit()
            db.refresh(row)
            return _model(row)

    def get(self, connection_id: str) -> Optional[ExternalKnowledgeConnectionModel]:
        with get_db() as db:
            row = db.get(ExternalKnowledgeConnection, connection_id)
            return _model(row) if row else None

    def get_auth_config(self, connection_id: str) -> Optional[dict[str, Any]]:
        with get_db() as db:
            row = db.get(ExternalKnowledgeConnection, connection_id)
            return _decrypt_auth_config(row.auth_config_encrypted) if row else None

    def list(self) -> list[ExternalKnowledgeConnectionModel]:
        with get_db() as db:
            rows = (
                db.query(ExternalKnowledgeConnection)
                .order_by(ExternalKnowledgeConnection.created_at.asc())
                .all()
            )
            return [_model(row) for row in rows]

    def update(
        self, connection_id: str, form: ExternalKnowledgeConnectionForm
    ) -> Optional[ExternalKnowledgeConnectionModel]:
        with get_db() as db:
            row = db.get(ExternalKnowledgeConnection, connection_id)
            if not row:
                return None
            values = form.model_dump(exclude={"auth_config"})
            values.update({"health": None, "updated_at": int(time.time())})
            if form.auth_config is not None:
                values["auth_config_encrypted"] = _encrypt_auth_config(form.auth_config)
            db.query(ExternalKnowledgeConnection).filter_by(id=connection_id).update(values)
            db.commit()
            return self.get(connection_id)

    def set_health(
        self, connection_id: str, health: dict[str, Any]
    ) -> Optional[ExternalKnowledgeConnectionModel]:
        with get_db() as db:
            updated = (
                db.query(ExternalKnowledgeConnection)
                .filter_by(id=connection_id)
                .update({"health": health})
            )
            db.commit()
            return self.get(connection_id) if updated else None

    def delete(self, connection_id: str) -> bool:
        with get_db() as db:
            deleted = (
                db.query(ExternalKnowledgeConnection)
                .filter_by(id=connection_id)
                .delete()
            )
            db.commit()
            return deleted == 1


ExternalKnowledgeConnections = ExternalKnowledgeConnectionsTable()
