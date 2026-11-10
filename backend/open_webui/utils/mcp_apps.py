# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded MCP application RPC support for configured tool servers."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Optional
from urllib.parse import urlsplit

import aiohttp

from open_webui.env import AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA


MAX_MCP_RESPONSE_BYTES = 5 * 1024 * 1024
_MCP_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _mcp_connection_id(connection: Any) -> Optional[str]:
    if not isinstance(connection, dict) or connection.get("type") != "mcp":
        return None
    info = connection.get("info")
    identifier = info.get("id") if isinstance(info, dict) else None
    if (
        not isinstance(identifier, str)
        or not 1 <= len(identifier) <= 128
        or identifier[0].isdigit()
        or any(character not in _MCP_ID_CHARS for character in identifier)
    ):
        return None
    return identifier


def resolve_mcp_connection(
    reference: str, connections: Any, user: Any
) -> Optional[tuple[dict[str, Any], str]]:
    """Resolve one unique, enabled and readable ``server:mcp:<id>`` entry."""
    prefix = "server:mcp:"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        return None
    identifier = reference[len(prefix) :]
    if not isinstance(connections, list):
        return None
    matches = [
        connection
        for connection in connections
        if _mcp_connection_id(connection) == identifier
    ]
    if len(matches) != 1:
        return None
    connection = matches[0]
    config = connection.get("config")
    if not isinstance(config, dict) or config.get("enable") is not True:
        return None
    if getattr(user, "role", None) != "admin":
        user_id = getattr(user, "id", None)
        grants = config.get("access_grants")
        if not isinstance(user_id, str) or not isinstance(grants, list):
            return None
        if not any(
            isinstance(grant, dict)
            and grant.get("principal_type") == "user"
            and grant.get("principal_id") == user_id
            and grant.get("permission") == "read"
            for grant in grants
        ):
            return None
    return connection, identifier


def create_mcp_app_binding(
    secret: str, user_id: str, server_id: str, tool_name: str
) -> str:
    """Create a restart-stable, user-bound capability without storing a token."""
    if not all(isinstance(value, str) and value for value in (secret, user_id, server_id, tool_name)):
        raise ValueError("MCP app bindings require non-empty string inputs")
    message = json.dumps(
        ["openatelier-mcp-app-v1", user_id, server_id, tool_name],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_mcp_app_binding(
    binding: str, secret: str, user_id: str, server_id: str, tool_name: str
) -> bool:
    if not isinstance(binding, str) or len(binding) != 64:
        return False
    try:
        expected = create_mcp_app_binding(
            secret, user_id, server_id, tool_name
        )
    except ValueError:
        return False
    return hmac.compare_digest(binding, expected)


def validate_mcp_app_request(tool_name: str, method: str, params: Any) -> dict[str, Any]:
    """Accept only the measured app bridge subset and exact parameter shapes."""
    if not isinstance(params, dict):
        raise ValueError("Invalid app request parameters")
    if method == "tools/call":
        if set(params) != {"name", "arguments"}:
            raise ValueError("Invalid app request parameters")
        if params.get("name") != tool_name or not isinstance(params.get("arguments"), dict):
            raise ValueError("Invalid app request parameters")
    elif method == "resources/list":
        if params:
            raise ValueError("Invalid app request parameters")
    elif method == "resources/read":
        if set(params) != {"uri"} or not isinstance(params.get("uri"), str) or not params["uri"]:
            raise ValueError("Invalid app request parameters")
    else:
        # The recent contract declares ui/resource, but its accepted parameter
        # shape has not yet been established. Fail closed instead of guessing.
        raise ValueError("Invalid app request parameters")
    return params


def _connection_url(connection: dict[str, Any]) -> str:
    url = connection.get("url")
    try:
        parsed = urlsplit(url) if isinstance(url, str) else None
    except ValueError as error:
        raise ValueError("Invalid MCP server URL") from error
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Invalid MCP server URL")
    return url


def _decode_json_rpc(raw: bytes, content_type: str) -> dict[str, Any]:
    if len(raw) > MAX_MCP_RESPONSE_BYTES:
        raise ValueError("MCP response exceeds the configured limit")
    if content_type.split(";", 1)[0].strip() == "text/event-stream":
        events = []
        for line in raw.decode("utf-8").splitlines():
            if line.startswith("data:"):
                events.append(line[5:].strip())
        if not events:
            raise ValueError("MCP server returned an empty event stream")
        raw = events[-1].encode("utf-8")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("MCP server returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("MCP server returned an invalid JSON-RPC response")
    return payload


async def call_mcp_method(
    connection: dict[str, Any], method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Initialize one bounded Streamable HTTP session and call one method."""
    url = _connection_url(connection)
    auth_type = connection.get("auth_type", "none")
    if auth_type not in {None, "none", "bearer"}:
        raise ValueError("Unsupported MCP authentication mode")
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if auth_type == "bearer":
        key = connection.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("Missing MCP bearer credential")
        headers["Authorization"] = f"Bearer {key}"

    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA)
    request_id = 1

    async def rpc(session: aiohttp.ClientSession, name: str, arguments: Optional[dict] = None):
        nonlocal request_id
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": name}
        request_id += 1
        if arguments is not None:
            body["params"] = arguments
        async with session.post(
            url,
            json=body,
            headers=headers,
            allow_redirects=False,
            max_line_size=16 * 1024,
            max_field_size=16 * 1024,
        ) as response:
            raw = await response.content.read(MAX_MCP_RESPONSE_BYTES + 1)
            if response.status != 200:
                raise ValueError(f"MCP server returned HTTP {response.status}")
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            payload = _decode_json_rpc(raw, response.headers.get("Content-Type", ""))
            if "error" in payload:
                error = payload.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                raise ValueError(message or "MCP server returned an error")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ValueError("MCP server returned an invalid result")
            return result

    async with aiohttp.ClientSession(timeout=timeout) as session:
        await rpc(
            session,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "OpenAtelierUI", "version": "0.1"},
            },
        )
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        async with session.post(
            url, json=notification, headers=headers, allow_redirects=False
        ) as response:
            await response.read()
            if response.status not in {200, 202, 204}:
                raise ValueError(f"MCP server returned HTTP {response.status}")
        return await rpc(session, method, params)


def shape_mcp_app_result(method: str, result: dict[str, Any]) -> dict[str, Any]:
    shaped = dict(result)
    shaped["resultType"] = "complete"
    if method in {"resources/list", "resources/read"}:
        shaped["cacheScope"] = "private"
        shaped["ttlMs"] = 0
    return shaped
