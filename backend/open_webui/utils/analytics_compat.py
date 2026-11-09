# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Provider-free analytics over locally persisted chat graphs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone


def _history_messages(chat):
    payload = getattr(chat, "chat", None)
    history = payload.get("history") if isinstance(payload, dict) else None
    messages = history.get("messages") if isinstance(history, dict) else None
    return [value for value in messages.values() if isinstance(value, dict)] if isinstance(messages, dict) else []


def _usage(message):
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    result = dict(usage)
    prompt = result.get("input_tokens", result.get("prompt_tokens", 0))
    completion = result.get("output_tokens", result.get("completion_tokens", 0))
    result["input_tokens"] = prompt if isinstance(prompt, int) else 0
    result["output_tokens"] = completion if isinstance(completion, int) else 0
    if not isinstance(result.get("total_tokens"), int):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def normalized_messages(chats, *, model_id=None, user_id=None, chat_id=None, start_date=None, end_date=None):
    if model_id is None and user_id is None and chat_id is None and start_date is None and end_date is None:
        return []
    rows = []
    for chat in chats:
        if user_id is not None and chat.user_id != user_id:
            continue
        if chat_id is not None and chat.id != chat_id:
            continue
        for message in _history_messages(chat):
            timestamp = message.get("timestamp")
            if not isinstance(timestamp, int):
                continue
            current_model = message.get("model")
            if model_id is not None and current_model != model_id:
                continue
            if start_date is not None and timestamp < start_date:
                continue
            if end_date is not None and timestamp > end_date:
                continue
            rows.append({
                "id": f"{chat.id}-{message.get('id')}", "chat_id": chat.id, "user_id": chat.user_id,
                "parent_id": message.get("parentId"), "role": str(message.get("role", "")), "content": message.get("content"),
                "model_id": current_model if isinstance(current_model, str) else None, "done": bool(message.get("done", True)),
                "error": message.get("error"), "meta": message.get("meta") if isinstance(message.get("meta"), dict) else None,
                "files": message.get("files") if isinstance(message.get("files"), list) else None,
                "sources": message.get("sources") if isinstance(message.get("sources"), list) else None,
                "status_history": message.get("statusHistory") if isinstance(message.get("statusHistory"), list) else None,
                "output": message.get("output") if isinstance(message.get("output"), list) else None,
                "embeds": message.get("embeds") if isinstance(message.get("embeds"), list) else None,
                "context_summary": message.get("context_summary") if isinstance(message.get("context_summary"), str) else None,
                "usage": _usage(message), "created_at": timestamp, "updated_at": chat.updated_at,
            })
    rows.sort(key=lambda item: item["created_at"], reverse=chat_id is None)
    return rows


def assistant_records(chats, *, start_date=None, end_date=None, user_ids=None):
    allowed = set(user_ids) if user_ids is not None else None
    records = []
    for chat in chats:
        if allowed is not None and chat.user_id not in allowed:
            continue
        for message in _history_messages(chat):
            timestamp, model = message.get("timestamp"), message.get("model")
            if message.get("role") != "assistant" or not isinstance(model, str) or not model or not isinstance(timestamp, int):
                continue
            if start_date is not None and timestamp < start_date:
                continue
            if end_date is not None and timestamp > end_date:
                continue
            records.append((chat, message, model, timestamp, _usage(message) or {}))
    return records


def summary(records):
    return {"total_messages": len(records), "total_chats": len({row[0].id for row in records}), "total_models": len({row[2] for row in records}), "total_users": len({row[0].user_id for row in records})}


def model_analytics(records):
    aggregate = {}
    for chat, _, model, _, _ in records:
        item = aggregate.setdefault(model, {"model_id": model, "count": 0, "chats": set(), "users": set()})
        item["count"] += 1; item["chats"].add(chat.id); item["users"].add(chat.user_id)
    return [{"model_id": model, "count": item["count"], "unique_chats": len(item["chats"]), "unique_users": len(item["users"])} for model, item in sorted(aggregate.items(), key=lambda pair: (-pair[1]["count"], pair[0]))]


def token_analytics(records):
    aggregate = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "message_count": 0})
    for _, _, model, _, usage in records:
        item = aggregate[model]
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key, 0); item[key] += value if isinstance(value, int) else 0
        item["message_count"] += 1
    models = [{"model_id": model, **values} for model, values in sorted(aggregate.items())]
    return {"models": models, "total_input_tokens": sum(item["input_tokens"] for item in models), "total_output_tokens": sum(item["output_tokens"] for item in models), "total_tokens": sum(item["total_tokens"] for item in models)}


def user_analytics(records, user_lookup):
    aggregate = defaultdict(lambda: {"count": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    for chat, _, _, _, usage in records:
        item = aggregate[chat.user_id]; item["count"] += 1
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key, 0); item[key] += value if isinstance(value, int) else 0
    rows = []
    for user_id, values in aggregate.items():
        user = user_lookup(user_id)
        rows.append({"user_id": user_id, "name": getattr(user, "name", None), "email": getattr(user, "email", None), **values})
    return sorted(rows, key=lambda item: (-item["count"], item["user_id"]))


def _bucket(timestamp, granularity):
    value = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    if granularity == "10m": value = value.replace(minute=(value.minute // 10) * 10, second=0, microsecond=0)
    elif granularity == "hourly": value = value.replace(minute=0, second=0, microsecond=0)
    elif granularity in {"3h", "6h"}:
        step = int(granularity[:-1]); value = value.replace(hour=(value.hour // step) * step, minute=0, second=0, microsecond=0)
    else: value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value.strftime("%Y-%m-%d %H:%M") if granularity != "daily" else value.strftime("%Y-%m-%d")


def daily_analytics(records, granularity):
    buckets = defaultdict(lambda: defaultdict(int))
    for _, _, model, timestamp, _ in records: buckets[_bucket(timestamp, granularity)][model] += 1
    return [{"date": date, "models": dict(sorted(models.items())), "start": None, "end": None, "previous": None} for date, models in sorted(buckets.items())]


def model_chats(chats, model_id, *, start_date=None, end_date=None, user_lookup=None):
    result = []
    for chat in chats:
        matched = any(row[2] == model_id for row in assistant_records([chat], start_date=start_date, end_date=end_date))
        if not matched: continue
        user = user_lookup(chat.user_id) if user_lookup else None
        result.append({"chat_id": chat.id, "user_id": chat.user_id, "user_name": getattr(user, "name", None), "first_message": chat.title, "updated_at": chat.updated_at})
    return result
