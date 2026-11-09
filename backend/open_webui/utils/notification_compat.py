# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Local notification-target state and bounded webhook delivery helpers."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from typing import Any
from urllib.parse import urlsplit

STATE_KEY = "openatelier_notification_targets"
SUPPORTED_EVENTS = (
    ("chat.finished", "Chat finished", "A chat response finished."),
    ("chat.failed", "Chat failed", "A chat response failed."),
    ("channel.message", "Channel message", "A channel message was posted."),
    ("calendar.alert", "Calendar alert", "A calendar event alert was triggered."),
    ("retrieval.content.process_failed", "Retrieval Content process failed", "Retrieval content processing failed."),
    ("automation.run_failed", "Automation run failed", "An automation run failed."),
)
SUPPORTED_EVENT_NAMES = frozenset(item[0] for item in SUPPORTED_EVENTS)


class NotificationInputError(ValueError):
    pass


def _settings(user: Any) -> dict:
    value = getattr(user, "settings", None)
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return dict(value) if isinstance(value, dict) else {}


def load_targets(user: Any) -> list[dict]:
    state = _settings(user).get(STATE_KEY, {})
    rows = state.get("targets", []) if isinstance(state, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def save_targets(user_id: str, rows: list[dict]) -> None:
    from open_webui.models.users import Users

    if Users.update_user_settings_by_id(user_id, {STATE_KEY: {"targets": rows}}) is None:
        raise RuntimeError("notification target state unavailable")


def public_target(row: dict) -> dict:
    url = row.get("url", "")
    parsed = urlsplit(url)
    tail = parsed.path[-4:] if parsed.path else ""
    masked = f"{parsed.scheme}://{parsed.netloc}/...{tail}"
    return {
        "id": row["id"],
        "type": row["type"],
        "enabled": row["enabled"],
        "events": list(row["events"]),
        "delivery": row["delivery"],
        "config": {"url_masked": masked},
        "is_default": row["is_default"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _resolved_public_addresses(hostname: str, port: int) -> list[str]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)})
    except OSError as error:
        raise NotificationInputError("invalid_url") from error
    if not addresses:
        raise NotificationInputError("invalid_url")
    for address in addresses:
        try:
            if not ipaddress.ip_address(address).is_global:
                raise NotificationInputError("invalid_url")
        except ValueError as error:
            raise NotificationInputError("invalid_url") from error
    return addresses


def validated_url(url: Any) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise NotificationInputError("invalid_url")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise NotificationInputError("invalid_url")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise NotificationInputError("invalid_url") from error
    _resolved_public_addresses(parsed.hostname, port)
    return url


def normalized_target(form: dict, *, previous: dict | None = None) -> dict:
    target_id = form.get("id")
    if target_id is None and previous is not None:
        target_id = previous.get("id")
    if not isinstance(target_id, str) or not target_id.strip() or len(target_id) > 128:
        raise NotificationInputError("invalid_id")
    normalized_id = re.sub(r"[^a-z0-9_-]+", "-", target_id.strip().lower()).strip("-")
    if not normalized_id:
        raise NotificationInputError("invalid_id")
    target_type = form.get("type", previous.get("type") if previous else None)
    if target_type != "webhook":
        raise NotificationInputError("unsupported_type")
    enabled = form.get("enabled", previous.get("enabled", True) if previous else True)
    if type(enabled) is not bool:
        raise NotificationInputError("invalid_enabled")
    events = form.get("events", previous.get("events", []) if previous else [])
    if not isinstance(events, list) or any(not isinstance(event, str) for event in events):
        raise NotificationInputError("invalid_events")
    unknown = next((event for event in events if event not in SUPPORTED_EVENT_NAMES), None)
    if unknown is not None:
        raise NotificationInputError(f"unsupported_event:{unknown}")
    delivery = form.get("delivery", previous.get("delivery", "away") if previous else "away")
    if delivery not in {"away", "always"}:
        raise NotificationInputError("invalid_delivery")
    config = form.get("config")
    if not isinstance(config, dict):
        raise NotificationInputError("invalid_url")
    supplied_url = config.get("url")
    if supplied_url is None and previous is not None and config.get("url_masked") == public_target(previous)["config"]["url_masked"]:
        supplied_url = previous.get("url")
    url = validated_url(supplied_url)
    now = int(time.time())
    return {
        "id": normalized_id,
        "type": target_type,
        "enabled": enabled,
        "events": list(dict.fromkeys(events)),
        "delivery": delivery,
        "url": url,
        "is_default": bool(previous and previous.get("is_default")),
        "created_at": previous.get("created_at", now) if previous else now,
        "updated_at": now,
    }


def deliver_test(row: dict) -> bool:
    """POST once to the already validated address, without redirects or proxies."""
    parsed = urlsplit(row["url"])
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolved_public_addresses(parsed.hostname, port)
    address = addresses[0]
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    body = json.dumps({"event": "test", "message": "OpenAtelierUI notification test"}).encode()
    headers = {"Host": parsed.netloc, "Content-Type": "application/json", "Content-Length": str(len(body))}
    connection = None
    try:
        if parsed.scheme == "https":
            raw = socket.create_connection((address, port), timeout=3)
            wrapped = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
            connection = http.client.HTTPConnection(address, port, timeout=3)
            connection.sock = wrapped
        else:
            connection = http.client.HTTPConnection(address, port, timeout=3)
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        response.read(65537)
        return 200 <= response.status < 300
    except (OSError, ssl.SSLError, http.client.HTTPException):
        return False
    finally:
        if connection is not None:
            connection.close()
