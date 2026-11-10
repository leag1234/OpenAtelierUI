# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured automation metadata/control compatibility routes."""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from dateutil.rrule import rrulestr
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import DATABASE_URL
from open_webui.models.automations import Automations, AutomationsUnavailable
from open_webui.models.chats import ChatForm, Chats
from open_webui.models.users import Users
from open_webui.utils.auth import get_verified_user
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.models import get_all_models


router = APIRouter()
automation_tasks = set()


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


@router.get("/{id}/runs/{run_id}", response_model=AutomationRunModel)
def get_automation_run(
    id: str,
    run_id: str,
    user=Depends(get_automation_admin),
):
    _owned(id, user)
    run = automation_store.get_run(id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def _completion_text(value):
    if not isinstance(value, dict):
        raise RuntimeError("Automation provider returned an unsupported response")
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("Automation provider returned no answer")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content:
        raise RuntimeError("Automation provider returned no answer")
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    return content, usage


def _chat_payload(automation, answer, usage):
    user_message_id, assistant_message_id = str(uuid.uuid4()), str(uuid.uuid4())
    prompt = automation["data"]["prompt"]
    model_id = automation["data"]["model_id"]
    timestamp = int(datetime.now(timezone.utc).timestamp())
    normalized_usage = {
        "prompt_tokens": usage.get("prompt_tokens", usage.get("input_tokens", 0)),
        "completion_tokens": usage.get("completion_tokens", usage.get("output_tokens", 0)),
        "total_tokens": usage.get("total_tokens", 0),
    }
    normalized_usage["input_tokens"] = normalized_usage["prompt_tokens"]
    normalized_usage["output_tokens"] = normalized_usage["completion_tokens"]
    user_message = {
        "id": user_message_id,
        "parentId": None,
        "childrenIds": [assistant_message_id],
        "role": "user",
        "content": prompt,
        "models": [model_id],
        "timestamp": timestamp,
    }
    assistant_message = {
        "id": assistant_message_id,
        "parentId": user_message_id,
        "childrenIds": [],
        "role": "assistant",
        "content": answer,
        "model": model_id,
        "done": True,
        "timestamp": timestamp,
        "usage": normalized_usage,
        "output": [
            {
                "id": "msg_" + uuid.uuid4().hex[:24],
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": answer}],
            }
        ],
    }
    return {
        "title": automation["name"],
        "models": [model_id],
        "history": {
            "messages": {
                user_message_id: user_message,
                assistant_message_id: assistant_message,
            },
            "currentId": assistant_message_id,
        },
        "messages": [{"role": "user", "content": prompt}],
        "meta": {"automation_id": automation["id"]},
    }


async def _execute_automation(request, automation, user):
    try:
        model_id = automation["data"]["model_id"]
        if model_id not in request.app.state.MODELS:
            models = await get_all_models(request, user=user)
            request.app.state.MODELS = {
                model["id"]: model for model in models if isinstance(model, dict) and model.get("id")
            }
        completion = await generate_chat_completion(
            request,
            {
                "model": model_id,
                "messages": [{"role": "user", "content": automation["data"]["prompt"]}],
                "stream": True,
            },
            user,
        )
        answer, usage = _completion_text(completion)
        chat = Chats.insert_new_chat(
            user.id, ChatForm(chat=_chat_payload(automation, answer, usage))
        )
        if chat is None:
            raise RuntimeError("Automation chat could not be saved")
        automation_store.record_run(automation["id"], "success", chat_id=chat.id)
    except Exception as error:
        message = str(error).strip() or error.__class__.__name__
        automation_store.record_run(automation["id"], "error", error=message[:1000])


def _scheduler_request(app):
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/api/v1/automations/scheduler",
            "raw_path": b"/api/v1/automations/scheduler",
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("scheduler", 0),
            "client": None,
            "root_path": "",
        }
    )


async def scheduler_loop(app, poll_ceiling=30.0):
    """Claim due rows atomically and recover missed work after restart."""
    if automation_store is None:
        return
    request = _scheduler_request(app)
    while True:
        claimed = automation_store.claim_due(time.time_ns())
        for automation in claimed:
            claimed_at = automation["last_run_at"]
            try:
                next_runs = _occurrences(automation["data"]["rrule"])
                automation_store.schedule_after_claim(
                    automation["id"], claimed_at, next_runs[0] if next_runs else None
                )
                user = Users.get_user_by_id(automation["user_id"])
                if user is None:
                    raise RuntimeError("Automation owner is unavailable")
                await _execute_automation(request, automation, user)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                message = str(error).strip() or error.__class__.__name__
                automation_store.record_run(
                    automation["id"], "error", error=message[:1000]
                )
        if claimed:
            continue
        next_due = automation_store.next_due()
        if next_due is None:
            delay = poll_ceiling
        else:
            delay = min(poll_ceiling, max(0.25, (next_due - time.time_ns()) / 1_000_000_000))
        await asyncio.sleep(delay)


@router.post("/{id}/run", response_model=AutomationResponse)
async def run_automation(request: Request, id: str, user=Depends(get_automation_admin)):
    current = _owned(id, user)
    updated = automation_store.mark_started(id, user.id)
    if updated is None:
        raise _not_found()
    task = asyncio.create_task(_execute_automation(request, current, user))
    automation_tasks.add(task)
    task.add_done_callback(automation_tasks.discard)
    return _response(updated)


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
