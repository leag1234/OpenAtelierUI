# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Provider-free evaluation projections measured against public runtimes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def _row(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    return dict(value)


def _data(row: dict) -> dict:
    value = row.get("data")
    return value if isinstance(value, dict) else {}


def _rating(row: dict) -> float | None:
    value = _data(row).get("rating")
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def id_projection(feedbacks: list[Any]) -> list[dict]:
    return [
        {name: row[name] for name in ("id", "user_id", "created_at", "updated_at")}
        for row in map(_row, feedbacks)
    ]


def model_ids(feedbacks: list[Any]) -> list[str]:
    return sorted(
        {
            model_id
            for row in map(_row, feedbacks)
            if isinstance((model_id := _data(row).get("model_id")), str) and model_id
        }
    )


def list_projection(
    feedbacks: list[Any],
    user_lookup: Callable[[str], Any],
    *,
    order_by: str | None = None,
    direction: str | None = None,
    page: int | None = 1,
    model_id: str | None = None,
    page_size: int = 50,
) -> dict:
    rows = [_row(item) for item in feedbacks]
    if model_id is not None:
        rows = [row for row in rows if _data(row).get("model_id") == model_id]
    key_name = order_by if order_by in {"created_at", "updated_at", "user_id", "type"} else "updated_at"
    rows.sort(key=lambda row: (row.get(key_name) is None, row.get(key_name)), reverse=direction != "asc")
    total = len(rows)
    page_number = max(page or 1, 1)
    rows = rows[(page_number - 1) * page_size : page_number * page_size]
    items = []
    for row in rows:
        user = user_lookup(row["user_id"])
        user_data = None
        if user is not None:
            user_row = _row(user)
            user_data = {
                name: user_row[name]
                for name in ("id", "name", "email", "role", "last_active_at", "updated_at", "created_at")
            }
        items.append(
            {
                name: row.get(name)
                for name in ("id", "user_id", "version", "type", "data", "meta", "created_at", "updated_at")
            }
            | {"user": user_data}
        )
    return {"items": items, "total": total}


def _elo(rating: int, opponent: int, score: float) -> int:
    expected = 1 / (1 + 10 ** ((opponent - rating) / 400))
    return round(rating + 32 * (score - expected))


def leaderboard(feedbacks: list[Any]) -> dict:
    ratings: dict[str, int] = {}
    won: dict[str, int] = {}
    lost: dict[str, int] = {}
    tag_counts: dict[str, dict[str, int]] = {}
    rows = sorted(map(_row, feedbacks), key=lambda row: row.get("created_at", 0))
    for row in rows:
        data = _data(row)
        model_id = data.get("model_id")
        siblings = data.get("sibling_model_ids")
        rating = _rating(row)
        if not isinstance(model_id, str) or not model_id or not isinstance(siblings, list) or rating in (None, 0):
            continue
        for sibling in siblings:
            if not isinstance(sibling, str) or not sibling or sibling == model_id:
                continue
            ratings.setdefault(model_id, 1000)
            ratings.setdefault(sibling, 1000)
            won.setdefault(model_id, 0)
            won.setdefault(sibling, 0)
            lost.setdefault(model_id, 0)
            lost.setdefault(sibling, 0)
            model_score = 1.0 if rating > 0 else 0.0
            model_rating, sibling_rating = ratings[model_id], ratings[sibling]
            ratings[model_id] = _elo(model_rating, sibling_rating, model_score)
            ratings[sibling] = _elo(sibling_rating, model_rating, 1.0 - model_score)
            winner, loser = (model_id, sibling) if model_score else (sibling, model_id)
            won[winner] += 1
            lost[loser] += 1
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
        for tag in tags:
            if isinstance(tag, str) and tag:
                tag_counts.setdefault(model_id, {})[tag] = tag_counts.setdefault(model_id, {}).get(tag, 0) + 1
    entries = []
    for model_id, rating in ratings.items():
        tags = sorted(tag_counts.get(model_id, {}).items(), key=lambda item: (-item[1], item[0]))[:5]
        entries.append({
            "model_id": model_id,
            "rating": rating,
            "won": won[model_id],
            "lost": lost[model_id],
            "count": won[model_id] + lost[model_id],
            "top_tags": [{"tag": tag, "count": count} for tag, count in tags],
        })
    entries.sort(key=lambda item: (-item["rating"], item["model_id"]))
    return {"entries": entries}


def model_history(feedbacks: list[Any], model_id: str, days: int, *, now: datetime | None = None) -> dict:
    if days <= 0:
        return {"model_id": model_id, "history": []}
    current = (now or datetime.now(timezone.utc)).date()
    start = current - timedelta(days=days - 1)
    counts = {start + timedelta(days=index): {"won": 0, "lost": 0} for index in range(days)}
    for row in map(_row, feedbacks):
        if _data(row).get("model_id") != model_id:
            continue
        rating = _rating(row)
        if rating in (None, 0):
            continue
        day = datetime.fromtimestamp(row.get("created_at", 0), timezone.utc).date()
        if day in counts:
            counts[day]["won" if rating > 0 else "lost"] += 1
    return {
        "model_id": model_id,
        "history": [{"date": day.isoformat(), **counts[day]} for day in sorted(counts)],
    }
