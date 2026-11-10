# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured notification-target and inbox compatibility routes."""

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.auth import get_verified_user
from open_webui.utils.notification_compat import (
    NotificationInputError,
    SUPPORTED_EVENTS,
    deliver_test,
    load_targets,
    normalized_target,
    public_target,
    save_targets,
)

router = APIRouter()


class NotificationTargetForm(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None
    enabled: Optional[bool] = None
    events: Optional[list[str]] = None
    delivery: Optional[str] = None
    config: Optional[dict] = None
    model_config = ConfigDict(extra="ignore")


@router.get("/inbox")
async def get_inbox(
    cursor: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    user=Depends(get_verified_user),
):
    # Populated-item storage is intentionally not fabricated until a public
    # producer exposes its item contract to the black-box qualification suite.
    return {"items": [], "next_cursor": None}


@router.get("/inbox/count")
async def get_inbox_count(user=Depends(get_verified_user)):
    return {"count": 0}


@router.post("/inbox/open")
async def open_inbox(user=Depends(get_verified_user)):
    return {
        "acknowledged_at": str(time.time_ns()),
        "acknowledged_count": 0,
        "count": 0,
        "items": [],
        "next_cursor": None,
    }


@router.patch("/inbox/{id}")
async def read_inbox_item(id: str, user=Depends(get_verified_user)):
    raise HTTPException(status_code=404, detail="Notification not found")


def _available(request: Request) -> None:
    if not request.app.state.config.ENABLE_USER_WEBHOOKS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)


def _permitted(request: Request, user) -> None:
    if user.role == "admin":
        return
    permissions = request.app.state.config.USER_PERMISSIONS
    if not isinstance(permissions, dict) or not permissions.get("features", {}).get("webhooks", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED)


def _guard(request: Request, user) -> None:
    _available(request)
    _permitted(request, user)


def _input_error(error: NotificationInputError) -> HTTPException:
    code = str(error)
    if code.startswith("unsupported_event:"):
        return HTTPException(status_code=400, detail="unsupported notification event: " + code.split(":", 1)[1])
    if code == "invalid_url":
        return HTTPException(status_code=400, detail="The URL you provided is invalid. Please double-check and try again.")
    return HTTPException(status_code=400, detail="Invalid notification target")


def _find(rows: list[dict], target_id: str) -> tuple[int, dict] | tuple[None, None]:
    for index, row in enumerate(rows):
        if row.get("id") == target_id:
            return index, row
    return None, None


@router.get("/events")
async def get_notification_events(request: Request, user=Depends(get_verified_user)):
    _guard(request, user)
    return {"events": [{"event": event, "label": label, "description": description} for event, label, description in SUPPORTED_EVENTS]}


@router.get("/targets")
async def get_notification_targets(request: Request, user=Depends(get_verified_user)):
    _guard(request, user)
    return {"targets": [public_target(row) for row in load_targets(user)]}


@router.post("/targets")
async def create_notification_target(request: Request, form_data: NotificationTargetForm, user=Depends(get_verified_user)):
    _guard(request, user)
    rows = load_targets(user)
    try:
        row = normalized_target(form_data.model_dump(exclude_unset=True))
    except NotificationInputError as error:
        raise _input_error(error) from None
    if any(item.get("id") == row["id"] for item in rows):
        raise HTTPException(status_code=400, detail="notification target id already exists")
    row["is_default"] = not rows
    rows.append(row)
    save_targets(user.id, rows)
    return public_target(row)


@router.put("/targets/{target_id}")
async def update_notification_target(request: Request, target_id: str, form_data: NotificationTargetForm, user=Depends(get_verified_user)):
    _guard(request, user)
    rows = load_targets(user)
    index, previous = _find(rows, target_id)
    if previous is None:
        raise HTTPException(status_code=400, detail="Notification target not found")
    try:
        updated = normalized_target(form_data.model_dump(exclude_unset=True), previous=previous)
    except NotificationInputError as error:
        raise _input_error(error) from None
    if updated["id"] != target_id and any(item.get("id") == updated["id"] for item in rows):
        raise HTTPException(status_code=400, detail="notification target id already exists")
    rows[index] = updated
    save_targets(user.id, rows)
    return public_target(updated)


@router.put("/targets/{target_id}/default")
async def set_default_notification_target(request: Request, target_id: str, user=Depends(get_verified_user)):
    _guard(request, user)
    rows = load_targets(user)
    index, selected = _find(rows, target_id)
    if selected is None:
        raise HTTPException(status_code=400, detail="Notification target not found")
    for row in rows:
        row["is_default"] = row.get("id") == target_id
    save_targets(user.id, rows)
    return public_target(rows[index])


@router.post("/targets/{target_id}/test")
async def test_notification_target(request: Request, target_id: str, user=Depends(get_verified_user)):
    _guard(request, user)
    _, selected = _find(load_targets(user), target_id)
    if selected is None:
        raise HTTPException(status_code=400, detail="Notification target not found")
    if not deliver_test(selected):
        raise HTTPException(status_code=400, detail="Webhook delivery failed")
    return {"ok": True}


@router.delete("/targets/{target_id}")
async def delete_notification_target(request: Request, target_id: str, user=Depends(get_verified_user)):
    _guard(request, user)
    rows = load_targets(user)
    index, selected = _find(rows, target_id)
    if selected is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    was_default = bool(selected.get("is_default"))
    del rows[index]
    if was_default and rows:
        rows[0]["is_default"] = True
    save_targets(user.id, rows)
    return {"ok": True}
