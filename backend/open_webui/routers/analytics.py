# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured provider-free local analytics compatibility routes."""

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from open_webui.models.chats import Chats
from open_webui.models.feedbacks import Feedbacks
from open_webui.models.folders import Folders
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.utils.analytics_compat import (
    assistant_records, daily_analytics, model_analytics, model_chats,
    normalized_messages, summary, token_analytics, user_analytics,
    session_event_rows, session_message_rows, session_overview, session_record,
    session_records, session_trace_rows, trace_chart,
)
from open_webui.utils.auth import get_admin_user

router = APIRouter()

class SummaryResponse(BaseModel):
    total_messages: int; total_chats: int; total_models: int; total_users: int
class DailyStatsEntry(BaseModel):
    date: str; models: dict[str, int]; start: Optional[str] = None; end: Optional[str] = None; previous: Optional[dict] = None
class DailyStatsResponse(BaseModel): data: list[DailyStatsEntry]
class ModelAnalyticsEntry(BaseModel):
    model_id: str; count: int; unique_chats: int = 0; unique_users: int = 0
class ModelAnalyticsResponse(BaseModel): models: list[ModelAnalyticsEntry]
class UserAnalyticsEntry(BaseModel):
    user_id: str; count: int; name: Optional[str] = None; email: Optional[str] = None; input_tokens: int = 0; output_tokens: int = 0; total_tokens: int = 0
class UserAnalyticsResponse(BaseModel): users: list[UserAnalyticsEntry]; total: int
class TokenUsageEntry(BaseModel):
    model_id: str; input_tokens: int; output_tokens: int; total_tokens: int; message_count: int
class TokenUsageResponse(BaseModel):
    models: list[TokenUsageEntry]; total_input_tokens: int; total_output_tokens: int; total_tokens: int
class ChatMessageModel(BaseModel):
    id: str; chat_id: str; user_id: str; parent_id: Optional[str] = None; role: str; content: Any = None; model_id: Optional[str] = None; done: bool = True
    error: Optional[dict | str] = None; meta: Optional[dict] = None; files: Optional[list] = None; sources: Optional[list] = None
    status_history: Optional[list] = None; output: Optional[list] = None; embeds: Optional[list] = None; context_summary: Optional[str] = None
    usage: Optional[dict] = None; created_at: int; updated_at: int
class ModelChatEntry(BaseModel):
    chat_id: str; user_id: Optional[str] = None; user_name: Optional[str] = None; first_message: Optional[str] = None; updated_at: int
class ModelChatsResponse(BaseModel): chats: list[ModelChatEntry]; total: int
class HistoryEntry(BaseModel): date: str; won: int = 0; lost: int = 0
class TagEntry(BaseModel): tag: str; count: int
class ModelOverviewResponse(BaseModel): history: list[HistoryEntry]; tags: list[TagEntry]

def _records(start_date=None, end_date=None, group_id=None):
    user_ids = None if group_id is None else (Groups.get_group_user_ids_by_id(group_id) or [])
    return assistant_records(Chats.get_chats(), start_date=start_date, end_date=end_date, user_ids=user_ids)

def _session_scope(start_date=None, end_date=None, group_id=None):
    user_ids = None if group_id is None else (Groups.get_group_user_ids_by_id(group_id) or [])
    return session_records(Chats.get_chats(), Users.get_user_by_id, Folders.get_folder_by_id, start_date=start_date, end_date=end_date, user_ids=user_ids)

def _session_chat(chat_id, start_date=None, end_date=None, group_id=None):
    chat = Chats.get_chat_by_id(chat_id)
    allowed = None if group_id is None else set(Groups.get_group_user_ids_by_id(group_id) or [])
    record = None if chat is None or (allowed is not None and chat.user_id not in allowed) else session_record(chat, Users.get_user_by_id, Folders.get_folder_by_id, start_date=start_date, end_date=end_date)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found in this range")
    return chat, record

@router.get("/summary", response_model=SummaryResponse)
async def get_analytics_summary(start_date: Optional[int] = None, end_date: Optional[int] = None, group_id: Optional[str] = None, user=Depends(get_admin_user)):
    return summary(_records(start_date, end_date, group_id))

@router.get("/daily", response_model=DailyStatsResponse)
async def get_daily_analytics(start_date: Optional[int] = None, end_date: Optional[int] = None, group_id: Optional[str] = None, granularity: Literal["10m", "hourly", "3h", "6h", "daily"] = "daily", user=Depends(get_admin_user)):
    return {"data": daily_analytics(_records(start_date, end_date, group_id), granularity)}

@router.get("/models", response_model=ModelAnalyticsResponse)
async def get_model_analytics(start_date: Optional[int] = None, end_date: Optional[int] = None, group_id: Optional[str] = None, user=Depends(get_admin_user)):
    return {"models": model_analytics(_records(start_date, end_date, group_id))}

@router.get("/users", response_model=UserAnalyticsResponse)
async def get_user_analytics(start_date: Optional[int] = None, end_date: Optional[int] = None, group_id: Optional[str] = None, limit: int = 50, user=Depends(get_admin_user)):
    rows = user_analytics(_records(start_date, end_date, group_id), Users.get_user_by_id)
    return {"users": rows[:max(0, limit)], "total": len(rows)}

@router.get("/tokens", response_model=TokenUsageResponse)
async def get_token_analytics(start_date: Optional[int] = None, end_date: Optional[int] = None, group_id: Optional[str] = None, user=Depends(get_admin_user)):
    return token_analytics(_records(start_date, end_date, group_id))

@router.get("/messages", response_model=list[ChatMessageModel])
async def get_analytics_messages(model_id: Optional[str] = None, user_id: Optional[str] = None, chat_id: Optional[str] = None, start_date: Optional[int] = None, end_date: Optional[int] = None, skip: int = 0, limit: int = Query(50, le=100), user=Depends(get_admin_user)):
    rows = normalized_messages(Chats.get_chats(), model_id=model_id, user_id=user_id, chat_id=chat_id, start_date=start_date, end_date=end_date)
    return rows[max(0, skip):max(0, skip) + max(0, limit)]

@router.get("/models/{model_id}/chats", response_model=ModelChatsResponse)
async def get_model_chats(model_id: str, start_date: Optional[int] = None, end_date: Optional[int] = None, skip: int = 0, limit: int = Query(50, le=100), order_by: str = "updated_at", direction: str = "desc", user=Depends(get_admin_user)):
    rows = model_chats(Chats.get_chats(), model_id, start_date=start_date, end_date=end_date, user_lookup=Users.get_user_by_id)
    reverse = direction != "asc"; rows.sort(key=lambda item: item.get(order_by) if order_by in item else item["updated_at"], reverse=reverse)
    return {"chats": rows[max(0, skip):max(0, skip) + max(0, limit)], "total": len(rows)}

@router.get("/models/{model_id}/overview", response_model=ModelOverviewResponse)
async def get_model_overview(model_id: str, days: int = 30, user=Depends(get_admin_user)):
    history, tags = {}, {}
    for feedback in Feedbacks.get_all_feedbacks():
        data, meta = feedback.data or {}, feedback.meta or {}
        if data.get("model_id") != model_id: continue
        date = __import__("datetime").datetime.fromtimestamp(feedback.created_at, tz=__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
        entry = history.setdefault(date, {"date": date, "won": 0, "lost": 0})
        rating = data.get("rating")
        if rating in (1, "1", "up", "won"): entry["won"] += 1
        elif rating in (-1, "-1", "down", "lost"): entry["lost"] += 1
        for tag in meta.get("tags", []) if isinstance(meta.get("tags"), list) else []:
            if isinstance(tag, str): tags[tag] = tags.get(tag, 0) + 1
    return {"history": [history[key] for key in sorted(history)], "tags": [{"tag": key, "count": value} for key, value in sorted(tags.items())]}

@router.get("/sessions")
async def get_sessions(
    start_date: Optional[int] = Query(None, ge=0), end_date: Optional[int] = Query(None, ge=0),
    group_id: Optional[str] = None, query: str = Query("", max_length=200), model_id: Optional[str] = None,
    hide_subagents: bool = False,
    order_by: Literal["title", "user", "recent", "tokens", "responses", "errors", "duration"] = "recent",
    direction: Literal["asc", "desc"] = "desc", include_summary: bool = False,
    token_min: Optional[int] = Query(None, ge=0), token_max: Optional[int] = Query(None, ge=1),
    skip: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=50), user=Depends(get_admin_user),
):
    rows = _session_scope(start_date, end_date, group_id)
    needle = query.casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join(str(row.get(key) or "") for key in ("title", "user_name", "model_id")).casefold()]
    if model_id is not None: rows = [row for row in rows if row["model_id"] == model_id]
    if hide_subagents: rows = [row for row in rows if row.get("kind") != "subagent"]
    if token_min is not None: rows = [row for row in rows if row["tokens"] >= token_min]
    if token_max is not None: rows = [row for row in rows if row["tokens"] < token_max]
    sort_key = {"title": "title", "user": "user_name", "recent": "last_active", "tokens": "tokens", "responses": "responses", "errors": "errors", "duration": "span_seconds"}[order_by]
    rows.sort(key=lambda row: (row.get(sort_key) is not None, row.get(sort_key), row["id"]), reverse=direction == "desc")
    result = {"items": rows[skip:skip + limit], "has_more": len(rows) > skip + limit}
    if include_summary: result["overview"] = session_overview(rows)
    return result

@router.get("/sessions/{chat_id}")
async def get_session_messages(
    chat_id: str, start_date: Optional[int] = Query(None, ge=0), end_date: Optional[int] = Query(None, ge=0),
    group_id: Optional[str] = None, skip: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=50),
    user=Depends(get_admin_user),
):
    chat, record = _session_chat(chat_id, start_date, end_date, group_id)
    rows = session_message_rows(chat, start_date=start_date, end_date=end_date)
    return {"session": record, "items": rows[skip:skip + limit], "has_more": len(rows) > skip + limit}

@router.get("/sessions/{chat_id}/messages/{message_id}/events")
async def get_session_events(
    chat_id: str, message_id: str, start_date: Optional[int] = Query(None, ge=0), end_date: Optional[int] = Query(None, ge=0),
    group_id: Optional[str] = None, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
    user=Depends(get_admin_user),
):
    chat = Chats.get_chat_by_id(chat_id)
    allowed = None if group_id is None else set(Groups.get_group_user_ids_by_id(group_id) or [])
    if chat is None or (allowed is not None and chat.user_id not in allowed) or session_record(chat, Users.get_user_by_id, Folders.get_folder_by_id, start_date=start_date, end_date=end_date) is None:
        raise HTTPException(status_code=404, detail="Message not found in this session")
    rows = session_event_rows(chat, message_id, start_date=start_date, end_date=end_date)
    if rows is None: raise HTTPException(status_code=404, detail="Message not found in this session")
    return {"items": rows[skip:skip + limit], "has_more": len(rows) > skip + limit}

@router.get("/sessions/{chat_id}/trace")
async def get_session_trace(
    chat_id: str, start_date: Optional[int] = Query(None, ge=0), end_date: Optional[int] = Query(None, ge=0),
    group_id: Optional[str] = None, skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=50),
    view: Literal["all", "tools", "errors"] = "all", include_chart: bool = False,
    user=Depends(get_admin_user),
):
    chat, record = _session_chat(chat_id, start_date, end_date, group_id)
    rows = session_trace_rows(chat, start_date=start_date, end_date=end_date, view=view)
    result = {"session": record, "items": rows[skip:skip + limit], "has_more": len(rows) > skip + limit}
    if include_chart: result["chart"] = trace_chart(chat, rows, start_date=start_date, end_date=end_date)
    return result
