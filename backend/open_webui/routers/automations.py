# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured automation metadata/control compatibility routes."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from dateutil.rrule import rrulestr
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import DATABASE_URL
from open_webui.models.automations import Automations, AutomationsUnavailable
from open_webui.utils.auth import get_verified_user


router = APIRouter()


def _load_store(database_url):
    try:
        return Automations(database_url)
    except AutomationsUnavailable:
        return None


automation_store = (
    _load_store(DATABASE_URL)
    if os.environ.get("OPENATELIER_AUTOMATION_COMPAT", "1") != "0"
    else None
)


class AutomationTarget(BaseModel):
    type: Literal["chat", "channel"] = "chat"
    channel_id: Optional[str] = None


class AutomationTerminalConfig(BaseModel):
    server_id: str
    cwd: Optional[str] = None


class AutomationData(BaseModel):
    prompt: str
    model_id: str
    rrule: str
    target: Optional[AutomationTarget] = None
    terminal: Optional[AutomationTerminalConfig] = None


class AutomationForm(BaseModel):
    name: str
    data: AutomationData
    folder_id: Optional[str] = None
    is_active: Optional[bool] = True
    meta: Optional[dict[str, Any]] = None


class AutomationRunModel(BaseModel):
    id: str
    automation_id: str
    status: str
    created_at: int
    chat_id: Optional[str] = None
    error: Optional[str] = None


class AutomationResponse(BaseModel):
    id: str
    user_id: str
    name: str
    data: dict[str, Any]
    is_active: bool
    created_at: int
    updated_at: int
    folder_id: Optional[str] = None
    meta: Optional[dict[str, Any]] = None
    last_run_at: Optional[int] = None
    next_run_at: Optional[int] = None
    next_runs: Optional[list[int]] = None
    last_run: Optional[AutomationRunModel] = None


def _available():
    if automation_store is None:
        raise HTTPException(status_code=503, detail="Automations unavailable")


def get_automation_admin(user=Depends(get_verified_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="401 Unauthorized")
    return user


def _not_found():
    return HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)


def _encode(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise HTTPException(status_code=422, detail="Automation contains unsupported data") from None


def _occurrences(rrule_text, count=2):
    if "COUNT=" in rrule_text.upper() and not any(
        line.upper().startswith("DTSTART") for line in rrule_text.splitlines()
    ):
        raise HTTPException(
            status_code=400,
            detail="RRULE with COUNT requires an explicit DTSTART line to anchor the occurrence window",
        )
    try:
        schedule = rrulestr(rrule_text, forceset=True)
        values = list(schedule.xafter(datetime.now(timezone.utc), count=count, inc=True))
    except (AttributeError, OverflowError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid recurrence rule") from None
    if not values:
        return []
    result = []
    for value in values:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result.append(int(value.timestamp() * 1_000_000_000))
    return result


def _data(form):
    return form.data.model_dump()


def _response(value, include_runs=True):
    if value is None:
        raise _not_found()
    result = dict(value)
    result["next_runs"] = _occurrences(result["data"]["rrule"]) if include_runs else None
    return result


@router.get(
    "/list",
    response_model=None,
    responses={
        200: {
            "content": {
                "application/json": {
                    "schema": {}
                }
            }
        }
    },
)
def list_automations(
    query: Optional[str] = None,
    status: Optional[str] = None,
    folder_id: Optional[str] = None,
    page: Optional[int] = 1,
    user=Depends(get_automation_admin),
):
    _available()
    items, total = automation_store.list(user.id, query, status, folder_id, page or 1)
    return {"items": [_response(value, include_runs=False) for value in items], "total": total}


@router.post("/create", response_model=AutomationResponse)
def create_automation(form: AutomationForm, user=Depends(get_automation_admin)):
    _available()
    values = _data(form)
    next_runs = _occurrences(values["rrule"])
    created = automation_store.create(
        user.id, form.name, form.folder_id, _encode(values), _encode(form.meta) if form.meta is not None else None,
        form.is_active is not False, next_runs[0] if next_runs else None,
    )
    result = dict(created)
    result["next_runs"] = next_runs
    return result


def _owned(automation_id, user):
    _available()
    value = automation_store.get(automation_id)
    if value is None or value["user_id"] != user.id:
        raise _not_found()
    return value


@router.get("/{id}", response_model=AutomationResponse)
def get_automation(id: str, user=Depends(get_automation_admin)):
    return _response(_owned(id, user))


@router.get("/{id}/runs", response_model=list[AutomationRunModel])
def get_automation_runs(
    id: str,
    skip: int = 0,
    limit: int = 50,
    user=Depends(get_automation_admin),
):
    _owned(id, user)
    return automation_store.runs(id, skip, limit)


@router.post("/{id}/run")
def run_automation(id: str, user=Depends(get_automation_admin)):
    _owned(id, user)
    raise HTTPException(
        status_code=503,
        detail="Automation execution is unavailable in this compatibility slice",
    )


@router.post("/{id}/toggle", response_model=AutomationResponse)
def toggle_automation(id: str, user=Depends(get_automation_admin)):
    current = _owned(id, user)
    next_runs = _occurrences(current["data"]["rrule"])
    updated = automation_store.toggle(id, user.id, next_runs[0] if next_runs else None)
    result = _response(updated)
    result["next_runs"] = next_runs
    return result


@router.post("/{id}/update", response_model=AutomationResponse)
def update_automation(id: str, form: AutomationForm, user=Depends(get_automation_admin)):
    _owned(id, user)
    values = _data(form)
    next_runs = _occurrences(values["rrule"])
    updated = automation_store.update(
        id, user.id, form.name, form.folder_id, _encode(values),
        _encode(form.meta) if form.meta is not None else None, form.is_active is not False,
        next_runs[0] if next_runs else None,
    )
    result = _response(updated)
    result["next_runs"] = next_runs
    return result


@router.delete("/{id}/delete")
def delete_automation(id: str, user=Depends(get_automation_admin)):
    _owned(id, user)
    return automation_store.delete(id, user.id)
