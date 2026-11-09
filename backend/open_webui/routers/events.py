# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Administratively managed event catalog and durable webhook compatibility API."""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from open_webui.utils.auth import get_admin_user
from open_webui.utils.notification_compat import NotificationInputError, validated_url


router = APIRouter()


EVENT_NAMES = [
    "system.startup.started", "system.startup.completed", "system.shutdown.started", "system.shutdown.completed",
    "config.imported", "config.updated", "config.webhook.updated", "config.connections.updated",
    "config.tool_servers.updated", "config.terminal_servers.updated", "config.code_execution.updated",
    "config.models.updated", "config.banners.updated", "config.suggestions.updated", "auth.signup",
    "auth.mfa.enrolled", "auth.mfa.replaced", "auth.mfa.failed", "auth.mfa.throttled",
    "auth.mfa.recovery_used", "auth.mfa.recovery_codes_regenerated", "auth.mfa.reset_requested",
    "auth.mfa.reset_completed", "auth.mfa.policy_changed", "auth.login", "auth.sessions_revoked", "auth.logout",
    "auth.password_changed", "auth.api_key.created", "auth.api_key.deleted", "auth.oauth_session.deleted",
    "user.created", "user.updated", "user.deleted", "user.role_updated", "user.status_updated",
    "user.settings_updated", "user.profile_updated", "user.permissions_updated", "group.created", "group.updated",
    "group.deleted", "group.member_added", "group.member_removed", "chat.created", "chat.finished", "chat.failed",
    "chat.imported", "chat.updated", "chat.deleted", "chat.deleted_all", "chat.compacted", "chat.pinned",
    "chat.unpinned", "chat.cloned", "chat.archived", "chat.unarchived", "chat.shared", "chat.unshared",
    "chat.folder_updated", "chat.tag_added", "chat.tag_removed", "message.created", "message.updated",
    "message.deleted", "message.event_received", "message.reaction_added", "message.reaction_removed",
    "message.pinned", "message.unpinned", "channel.created", "channel.updated", "channel.deleted",
    "channel.member_added", "channel.member_removed", "channel.member_active_updated", "channel.message",
    "channel.webhook.created", "channel.webhook.updated", "channel.webhook.deleted", "file.uploaded",
    "file.content_updated", "file.renamed", "file.deleted", "file.deleted_all", "folder.created", "folder.updated",
    "folder.parent_updated", "folder.access_updated", "folder.deleted", "note.created", "note.updated",
    "note.access_updated", "note.pinned", "note.unpinned", "note.deleted", "memory.created", "memory.updated",
    "memory.deleted", "memory.reset", "knowledge.created", "knowledge.updated", "knowledge.deleted",
    "knowledge.reset", "knowledge.reindexed", "knowledge.access_updated", "knowledge.file.added",
    "knowledge.file.updated", "knowledge.file.removed", "knowledge.file.moved", "knowledge.directory.created",
    "knowledge.directory.updated", "knowledge.directory.deleted", "knowledge.external_connection.created",
    "knowledge.external_connection.updated", "knowledge.external_connection.deleted", "retrieval.content.processed",
    "retrieval.content.process_failed", "retrieval.collection.deleted", "retrieval.vector_db.reset",
    "retrieval.uploads.reset", "model.created", "model.imported", "model.synced", "model.updated", "model.deleted",
    "model.enabled", "model.disabled", "model.access_updated", "model.provider_config.updated",
    "model.provider_request.failed", "model.provider_model.created", "model.provider_model.deleted",
    "function.created", "function.updated", "function.deleted", "function.enabled", "function.disabled",
    "function.enable_started", "function.disable_started", "function.valves_updated", "tool.created", "tool.updated",
    "tool.deleted", "tool.access_updated", "tool.valves_updated", "skill.created", "skill.updated", "skill.deleted",
    "skill.enabled", "skill.disabled", "prompt.created", "prompt.updated", "prompt.deleted", "prompt.enabled",
    "prompt.disabled", "prompt.version_updated", "prompt.access_updated", "pipeline.uploaded", "pipeline.added",
    "pipeline.deleted", "pipeline.valves_updated", "calendar.created", "calendar.updated", "calendar.deleted",
    "calendar.default_updated", "calendar.event.created", "calendar.event.updated", "calendar.event.deleted",
    "calendar.event.rsvp_updated", "calendar.alert", "automation.created", "automation.updated", "automation.enabled",
    "automation.disabled", "automation.deleted", "automation.run_started", "automation.run_completed",
    "automation.run_failed", "feedback.created", "feedback.updated", "feedback.deleted", "feedback.deleted_all",
    "image.generated", "image.edited", "audio.speech_requested", "audio.transcription_requested",
    "terminal.session.opened", "terminal.session.closed", "notification.test", "notification.manual",
]


class EventWebhookForm(BaseModel):
    url: str
    name: Optional[str] = None
    events: Optional[list[str]] = None
    targets: Optional[list[dict[str, str]]] = None
    enabled: bool = True
    model_config = ConfigDict(extra="ignore")


class EventWebhookUpdateForm(BaseModel):
    url: Optional[str] = None
    name: Optional[str] = None
    events: Optional[list[str]] = None
    targets: Optional[list[dict[str, str]]] = None
    enabled: Optional[bool] = None
    model_config = ConfigDict(extra="ignore")


def _catalog_entry(event: str) -> dict:
    message = event.replace(".", " ").replace("_", " ").capitalize()
    return {"event": event, "message": message, "description": f"Notification emitted for {event}."}


def _rows(request: Request) -> list[dict]:
    rows = request.app.state.config.EVENT_WEBHOOKS
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def _save(request: Request, rows: list[dict]) -> None:
    request.app.state.config.EVENT_WEBHOOKS = rows


def _invalid_url() -> HTTPException:
    return HTTPException(status_code=400, detail="The URL you provided is invalid. Please double-check and try again.")


def _checked_url(url: str) -> str:
    try:
        return validated_url(url)
    except NotificationInputError:
        raise _invalid_url() from None


def _find(rows: list[dict], webhook_id: str) -> tuple[int, dict] | tuple[None, None]:
    for index, row in enumerate(rows):
        if row.get("id") == webhook_id:
            return index, row
    return None, None


@router.get("")
async def get_event_catalog(user=Depends(get_admin_user)):
    return {"schema": "0.12.5", "events": [_catalog_entry(event) for event in EVENT_NAMES]}


@router.get("/webhooks")
async def get_event_webhooks_api(request: Request, user=Depends(get_admin_user)):
    return _rows(request)


@router.post("/webhooks")
async def create_event_webhook(request: Request, form_data: EventWebhookForm, user=Depends(get_admin_user)):
    now = int(time.time())
    row = {
        "id": str(uuid.uuid4()),
        "url": _checked_url(form_data.url),
        "name": form_data.name or "Webhook",
        "events": form_data.events or ["*"],
        "targets": form_data.targets,
        "enabled": form_data.enabled,
        "created_at": now,
        "updated_at": now,
    }
    rows = _rows(request)
    rows.append(row)
    _save(request, rows)
    return row


@router.put("/webhooks/{webhook_id}")
async def update_event_webhook(request: Request, webhook_id: str, form_data: EventWebhookUpdateForm, user=Depends(get_admin_user)):
    rows = _rows(request)
    index, previous = _find(rows, webhook_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    supplied = form_data.model_dump(exclude_unset=True)
    if supplied.get("url") is not None:
        supplied["url"] = _checked_url(supplied["url"])
    for key, value in supplied.items():
        if value is not None:
            previous[key] = value
    previous["updated_at"] = int(time.time())
    rows[index] = previous
    _save(request, rows)
    return previous


@router.delete("/webhooks/{webhook_id}")
async def delete_event_webhook_api(request: Request, webhook_id: str, user=Depends(get_admin_user)):
    rows = _rows(request)
    index, previous = _find(rows, webhook_id)
    if previous is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    del rows[index]
    _save(request, rows)
    return {"status": True}
