# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Provider-free analytics over locally persisted chat graphs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json


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


def _session_messages(chat, start_date=None, end_date=None):
    messages = []
    for message in _history_messages(chat):
        timestamp = message.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            continue
        if start_date is not None and timestamp < start_date:
            continue
        if end_date is not None and timestamp > end_date:
            continue
        messages.append(message)
    return sorted(messages, key=lambda item: item["timestamp"])


def session_record(chat, user_lookup, folder_lookup=None, *, start_date=None, end_date=None):
    messages = _session_messages(chat, start_date, end_date)
    if not messages:
        return None
    responses = [message for message in messages if message.get("role") == "assistant"]
    models = sorted({message.get("model") for message in responses if isinstance(message.get("model"), str) and message.get("model")})
    usages = [_usage(message) for message in responses]
    recorded = [usage for usage in usages if usage is not None]
    cache_read_values, cache_write_values = [], []
    for usage in recorded:
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        read = usage.get("cache_read_input_tokens", details.get("cached_tokens"))
        write = usage.get("cache_creation_input_tokens")
        if isinstance(read, int): cache_read_values.append(read)
        if isinstance(write, int): cache_write_values.append(write)
    user = user_lookup(chat.user_id)
    folder = folder_lookup(chat.folder_id) if folder_lookup and getattr(chat, "folder_id", None) else None
    payload = chat.chat if isinstance(chat.chat, dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    started, ended = messages[0]["timestamp"], messages[-1]["timestamp"]
    return {
        "id": chat.id,
        "title": chat.title,
        "user_id": chat.user_id,
        "user_name": getattr(user, "name", None),
        "folder_name": getattr(folder, "name", None),
        "kind": meta.get("kind"),
        "forked_from": meta.get("forked_from"),
        "started_at": started,
        "last_active": ended,
        "span_seconds": max(0, ended - started),
        "messages": len(messages),
        "prompts": sum(message.get("role") == "user" for message in messages),
        "responses": len(responses),
        "complete_responses": sum(bool(message.get("done", True)) for message in responses),
        "recorded_responses": len(recorded),
        "errors": sum(bool(message.get("error")) for message in responses),
        "tokens": sum(usage.get("total_tokens", 0) for usage in recorded),
        "model_count": len(models),
        "model_id": models[0] if len(models) == 1 else None,
        "cache_read": sum(cache_read_values) if cache_read_values else None,
        "cache_read_responses": len(cache_read_values),
        "cache_write": sum(cache_write_values) if cache_write_values else None,
        "cache_write_responses": len(cache_write_values),
    }


def session_records(chats, user_lookup, folder_lookup=None, *, start_date=None, end_date=None, user_ids=None):
    allowed = set(user_ids) if user_ids is not None else None
    records = []
    for chat in chats:
        if allowed is not None and chat.user_id not in allowed:
            continue
        record = session_record(chat, user_lookup, folder_lookup, start_date=start_date, end_date=end_date)
        if record is not None:
            records.append(record)
    return records


def session_overview(records):
    tokens = [record["tokens"] for record in records]
    distribution = [0] * 6
    for value in tokens:
        boundary = 0
        for threshold in (1, 100, 1000, 10000, 100000):
            if value >= threshold: boundary += 1
        distribution[boundary] += 1
    return {
        "sessions": len(records),
        "prompts": sum(record["prompts"] for record in records),
        "responses": sum(record["responses"] for record in records),
        "complete_responses": sum(record["complete_responses"] for record in records),
        "recorded_responses": sum(record["recorded_responses"] for record in records),
        "errors": sum(record["errors"] for record in records),
        "tokens": sum(tokens),
        "max_tokens": max(tokens, default=0),
        "start": min((record["started_at"] for record in records), default=None),
        "end": max((record["last_active"] for record in records), default=None),
        "token_distribution": distribution,
        "unrecorded_sessions": sum(record["recorded_responses"] == 0 for record in records),
    }


def session_message_rows(chat, *, start_date=None, end_date=None):
    rows = []
    for message in _session_messages(chat, start_date, end_date):
        content = message.get("content")
        preview = content if isinstance(content, str) else json.dumps(content, separators=(",", ":"), ensure_ascii=False)
        truncated = len(preview) > 240
        usage = _usage(message)
        rows.append({
            "id": f"{chat.id}-{message.get('id')}",
            "parent_id": message.get("parentId"),
            "role": message.get("role"),
            "preview": preview[:240],
            "preview_truncated": truncated,
            "model_id": message.get("model") if isinstance(message.get("model"), str) else None,
            "done": bool(message.get("done", True)),
            "error": json.dumps(message.get("error"), separators=(",", ":"), ensure_ascii=False)
            if isinstance(message.get("error"), (dict, list)) else message.get("error"),
            "tokens": usage.get("total_tokens") if usage is not None else None,
            "output_count": len(message.get("output")) if isinstance(message.get("output"), list) else 0,
            "created_at": message["timestamp"],
        })
    return rows


def _duration(item):
    start, end = item.get("started_at"), item.get("ended_at")
    return end - start if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None


def output_event(item, index):
    content = item.get("content")
    details = json.dumps(content, separators=(",", ":"), ensure_ascii=False) if content is not None else None
    error = item.get("error")
    error_type = error.get("type") if isinstance(error, dict) else None
    return {
        "type": item.get("type"), "status": item.get("status"), "name": item.get("name"),
        "call_id": item.get("call_id", item.get("id") if item.get("type") == "tool" else None),
        "started_at": item.get("started_at"), "ended_at": item.get("ended_at"),
        "duration_seconds": _duration(item), "error_type": error_type,
        "details": details, "truncated": details is not None, "index": index,
    }


def session_event_rows(chat, composite_message_id, *, start_date=None, end_date=None):
    message = next((item for item in _session_messages(chat, start_date, end_date) if f"{chat.id}-{item.get('id')}" == composite_message_id), None)
    if message is None:
        return None
    output = message.get("output") if isinstance(message.get("output"), list) else []
    return [output_event(item, index) for index, item in enumerate(output) if isinstance(item, dict)]


def session_trace_rows(chat, *, start_date=None, end_date=None, view="all"):
    rows = []
    for message in _session_messages(chat, start_date, end_date):
        if message.get("role") != "assistant":
            continue
        message_id, model, created = f"{chat.id}-{message.get('id')}", message.get("model"), message["timestamp"]
        response = {
            "type": "response", "status": "failed" if message.get("error") else "completed",
            "name": "assistant", "call_id": None, "started_at": None, "ended_at": None,
            "duration_seconds": None, "error_type": None, "index": -1,
            "message_id": message_id, "model_id": model, "created_at": created, "result_index": None,
        }
        candidates = [response]
        for index, item in enumerate(message.get("output") if isinstance(message.get("output"), list) else []):
            if not isinstance(item, dict): continue
            event = output_event(item, index)
            event.pop("details"); event.pop("truncated")
            event.update({"message_id": message_id, "model_id": model, "created_at": created, "result_index": item.get("result_index")})
            candidates.append(event)
        for item in candidates:
            if view == "tools" and item.get("type") != "tool": continue
            if view == "errors" and item.get("status") != "failed" and not item.get("error_type"): continue
            rows.append(item)
    return rows


def trace_chart(chat, trace_rows, *, start_date=None, end_date=None):
    messages = [message for message in _session_messages(chat, start_date, end_date) if message.get("role") == "assistant"]
    by_model = {}
    session_start = min((message["timestamp"] for message in _session_messages(chat, start_date, end_date)), default=0)
    for message in messages:
        model = message.get("model")
        usage = _usage(message)
        if not isinstance(model, str) or usage is None: continue
        item = by_model.setdefault(model, {"model_id": model, "input_tokens": 0, "output_tokens": 0, "recorded": 0, "complete": 0, "errors": 0, "first": None, "last": None, "offset": 0})
        item["input_tokens"] += usage.get("input_tokens", 0); item["output_tokens"] += usage.get("output_tokens", 0)
        item["recorded"] += 1; item["complete"] += bool(message.get("done", True)); item["errors"] += bool(message.get("error"))
        offset = message["timestamp"] - session_start
        item["first"] = offset if item["first"] is None else min(item["first"], offset)
        item["last"] = offset if item["last"] is None else max(item["last"], offset)
    timed = [row for row in trace_rows if row.get("duration_seconds") is not None]
    starts = [row["started_at"] for row in timed if row.get("started_at") is not None]
    ends = [row["ended_at"] for row in timed if row.get("ended_at") is not None]
    return {
        "start": min(starts, default=None), "end": max(ends, default=None),
        "timed_operations": len(timed),
        "duration_operations": sum(row["duration_seconds"] for row in timed),
        "max_duration": max((row["duration_seconds"] for row in timed), default=None),
        "usage": list(by_model.values()),
    }
