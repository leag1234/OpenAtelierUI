# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Small, storage-compatible projections for the measured user-profile API."""

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


STATUS_KEYS = ("status_emoji", "status_message", "status_expires_at")
INTERNAL_VARIABLES_KEY = "_openatelier_user_variables"


class UserStatus(BaseModel):
    status_emoji: Optional[str] = None
    status_message: Optional[str] = None
    status_expires_at: Optional[int] = None


class UserVariablesForm(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class UserVariablesResponse(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


class UserUsageTotals(BaseModel):
    messages: int = 0
    user_messages: int = 0
    assistant_messages: int = 0
    total_chats: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    lifetime_tokens: int = 0
    models_used: int = 0
    active_days: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    peak_daily_tokens: int = 0
    longest_chat_seconds: int = 0


class UserUsageHeatmapEntry(BaseModel):
    date: str
    messages: int = 0
    tokens: int = 0
    chats: int = 0
    models: dict[str, int] = Field(default_factory=dict)


class UserUsageInsights(BaseModel):
    average_messages_per_active_day: float = 0
    average_tokens_per_chat: float = 0
    user_message_share: float = 0
    assistant_message_share: float = 0
    most_used_model: Optional[str] = None


class UserUsageModelEntry(BaseModel):
    model_id: str
    messages: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class UserUsageToolEntry(BaseModel):
    name: str
    count: int


class UserUsagePeriod(BaseModel):
    start_date: int
    end_date: int
    days: int


class UserUsageResponse(BaseModel):
    totals: UserUsageTotals
    heatmap: list[UserUsageHeatmapEntry]
    weekly_heatmap: list[UserUsageHeatmapEntry]
    cumulative_heatmap: list[UserUsageHeatmapEntry]
    insights: UserUsageInsights
    top_models: list[UserUsageModelEntry]
    top_tools: list[UserUsageToolEntry] = Field(default_factory=list)
    period: UserUsagePeriod


def safe_info(user) -> dict:
    return dict(user.info) if isinstance(getattr(user, "info", None), dict) else {}


def public_info(user) -> Optional[dict]:
    info = safe_info(user)
    for key in (*STATUS_KEYS, INTERNAL_VARIABLES_KEY):
        info.pop(key, None)
    return info or None


def merge_public_info(existing: Any, update: dict) -> dict:
    if any(key in update for key in (*STATUS_KEYS, INTERNAL_VARIABLES_KEY)):
        raise ValueError("Reserved user info field")
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(update)
    return merged


def status_projection(user) -> dict:
    info = safe_info(user)
    settings = user.settings.model_dump() if hasattr(user.settings, "model_dump") else user.settings
    settings = settings if isinstance(settings, dict) else None
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "profile_image_url": user.profile_image_url,
        "last_active_at": user.last_active_at,
        "updated_at": user.updated_at,
        "created_at": user.created_at,
        "username": None,
        "bio": getattr(user, "bio", None),
        "gender": getattr(user, "gender", None),
        "date_of_birth": getattr(user, "date_of_birth", None),
        "profile_banner_image_url": None,
        "timezone": (settings or {}).get("timezone"),
        "presence_state": None,
        "status_emoji": info.get("status_emoji"),
        "status_message": info.get("status_message"),
        "status_expires_at": info.get("status_expires_at"),
        "oauth": None,
        "info": public_info(user),
        "settings": settings,
        "scim": None,
    }


def update_status_info(info: Any, status: UserStatus) -> dict:
    updated = dict(info) if isinstance(info, dict) else {}
    updated.update(status.model_dump())
    return updated


def read_variables(user) -> dict[str, str]:
    info = safe_info(user)
    variables = info.get(INTERNAL_VARIABLES_KEY, {})
    if not isinstance(variables, dict):
        return {}
    return {key: value for key, value in variables.items() if isinstance(key, str) and isinstance(value, str)}


def validate_variables(variables: dict[str, Any], existing: dict[str, str]) -> dict[str, str]:
    allowed = set(existing)
    result = {}
    for key, value in variables.items():
        if key not in allowed:
            raise ValueError(f"Invalid user variable key: {key}")
        if not isinstance(value, str):
            raise ValueError(f"Invalid user variable value: {key}")
        result[key] = value
    return result


def update_variables_info(info: Any, variables: dict[str, str]) -> dict:
    updated = dict(info) if isinstance(info, dict) else {}
    updated[INTERNAL_VARIABLES_KEY] = dict(variables)
    return updated


def _messages(chat) -> list[dict]:
    payload = getattr(chat, "chat", None)
    if not isinstance(payload, dict):
        return []
    history = payload.get("history", {})
    raw = history.get("messages", {}) if isinstance(history, dict) else {}
    if isinstance(raw, dict):
        return [item for item in raw.values() if isinstance(item, dict)]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def build_usage(chats, days: int, now: Optional[int] = None) -> UserUsageResponse:
    end = int(now if now is not None else datetime.now(tz=timezone.utc).timestamp())
    start = end - days * 86400
    day_start = datetime.fromtimestamp(end, tz=timezone.utc).date() - timedelta(days=days - 1)
    rows = {
        (day_start + timedelta(days=index)).isoformat(): UserUsageHeatmapEntry(
            date=(day_start + timedelta(days=index)).isoformat()
        )
        for index in range(days)
    }
    role_counts = Counter()
    model_counts = Counter()
    model_tokens = Counter()
    tool_counts = Counter()
    total_chats = 0
    longest_chat_seconds = 0
    for chat in chats:
        created = int(getattr(chat, "created_at", 0) or 0)
        updated = int(getattr(chat, "updated_at", created) or created)
        messages = _messages(chat)
        if updated < start or created > end:
            continue
        total_chats += 1
        longest_chat_seconds = max(longest_chat_seconds, max(0, updated - created))
        date = datetime.fromtimestamp(max(start, created), tz=timezone.utc).date().isoformat()
        row = rows.get(date)
        if row is not None:
            row.chats += 1
        for message in messages:
            timestamp = int(message.get("timestamp") or message.get("created_at") or created)
            if timestamp < start or timestamp > end:
                continue
            role = message.get("role")
            if role in {"user", "assistant"}:
                role_counts[role] += 1
            model = message.get("model") or message.get("model_id")
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            if isinstance(model, str) and model:
                model_counts[model] += 1
                model_tokens[(model, "input")] += input_tokens
                model_tokens[(model, "output")] += output_tokens
            tool = message.get("tool") or message.get("tool_name")
            if isinstance(tool, str) and tool:
                tool_counts[tool] += 1
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
            row = rows.get(date)
            if row is not None:
                row.messages += 1
                row.tokens += input_tokens + output_tokens
                if isinstance(model, str) and model:
                    row.models[model] = row.models.get(model, 0) + 1
    heatmap = list(rows.values())
    active_days = sum(1 for row in heatmap if row.messages or row.chats)
    total_messages = role_counts["user"] + role_counts["assistant"]
    input_tokens = sum(value for (model, kind), value in model_tokens.items() if kind == "input")
    output_tokens = sum(value for (model, kind), value in model_tokens.items() if kind == "output")
    lifetime = input_tokens + output_tokens
    top_models = [
        UserUsageModelEntry(
            model_id=model,
            messages=count,
            input_tokens=model_tokens[(model, "input")],
            output_tokens=model_tokens[(model, "output")],
            total_tokens=model_tokens[(model, "input")] + model_tokens[(model, "output")],
        )
        for model, count in model_counts.most_common()
    ]
    weekly = []
    cursor = day_start - timedelta(days=day_start.weekday())
    final_date = day_start + timedelta(days=days - 1)
    while cursor <= final_date:
        weekly.append(UserUsageHeatmapEntry(date=cursor.isoformat()))
        cursor += timedelta(days=7)
    share = float(total_messages or 1)
    return UserUsageResponse(
        totals=UserUsageTotals(
            messages=total_messages,
            user_messages=role_counts["user"],
            assistant_messages=role_counts["assistant"],
            total_chats=total_chats,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            lifetime_tokens=lifetime,
            models_used=len(model_counts),
            active_days=active_days,
            peak_daily_tokens=max((row.tokens for row in heatmap), default=0),
            longest_chat_seconds=longest_chat_seconds,
        ),
        heatmap=heatmap,
        weekly_heatmap=weekly,
        cumulative_heatmap=[row.model_copy(deep=True) for row in heatmap],
        insights=UserUsageInsights(
            average_messages_per_active_day=(total_messages / active_days if active_days else 0),
            average_tokens_per_chat=(lifetime / total_chats if total_chats else 0),
            user_message_share=role_counts["user"] / share,
            assistant_message_share=role_counts["assistant"] / share,
            most_used_model=top_models[0].model_id if top_models else None,
        ),
        top_models=top_models,
        top_tools=[UserUsageToolEntry(name=name, count=count) for name, count in tool_counts.most_common()],
        period=UserUsagePeriod(start_date=start, end_date=end, days=days),
    )
