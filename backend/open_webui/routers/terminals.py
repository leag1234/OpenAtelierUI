# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded terminal compatibility proxy with a hardened attachment path."""

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from open_webui.utils.auth import get_verified_user
from open_webui.utils.terminal_download_policy import (
    TerminalPolicyError,
    build_download_request,
    public_terminal_connections,
    parse_terminal_connections,
)


router = APIRouter()
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_PROXY_BYTES = 64 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 64 * 1024
_FETCH_LIMIT = threading.BoundedSemaphore(4)


@asynccontextmanager
async def _fetch_slot(timeout):
    deadline = asyncio.get_running_loop().time() + timeout
    while not _FETCH_LIMIT.acquire(blocking=False):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError()
        await asyncio.sleep(min(0.01, remaining))
    try:
        yield max(0.001, deadline - asyncio.get_running_loop().time())
    finally:
        _FETCH_LIMIT.release()


def _connection_json():
    return os.environ.get("TERMINAL_SERVER_CONNECTIONS", "[]")


def _http_error(status, detail):
    raise HTTPException(status_code=status, detail=detail)


def _proxy_error(status, message):
    return JSONResponse(status_code=status, content={"error": message})


def _download_headers(upstream, filename, body_length=None):
    types = upstream.headers.getall("Content-Type", [])
    content_type = types[0].split(";", 1)[0].strip().lower() if len(types) == 1 else ""
    if content_type not in {"application/pdf", "image/png", "image/jpeg", "text/plain"}:
        content_type = "application/octet-stream"
    headers = {
        "Content-Type": content_type,
        "Content-Disposition": "attachment; filename=\"download\"; filename*=UTF-8''" + quote(filename, safe=""),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "no-store",
    }
    if body_length is not None:
        headers["Content-Length"] = str(body_length)
    return headers


def _declared_length(headers):
    values = headers.getall("Content-Length", [])
    if not values:
        return None
    if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
        raise HTTPException(502, detail="Terminal file response is unavailable.")
    size = int(values[0])
    if size > MAX_DOWNLOAD_BYTES:
        raise HTTPException(413, detail="Terminal file exceeds the download limit.")
    return size


async def _read_bounded(response):
    body = bytearray()
    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
        if len(body) + len(chunk) > MAX_DOWNLOAD_BYTES:
            raise HTTPException(413, detail="Terminal file exceeds the download limit.")
        body.extend(chunk)
    return bytes(body)


async def _read_proxy_response(response):
    body = bytearray()
    async for chunk in response.content.iter_chunked(DOWNLOAD_CHUNK_BYTES):
        if len(body) + len(chunk) > MAX_PROXY_BYTES:
            raise HTTPException(413, detail="Terminal response exceeds the transfer limit.")
        body.extend(chunk)
    return bytes(body)


def _proxy_response_headers(upstream):
    headers = {}
    fixed = {
        "accept-ranges", "cache-control", "content-disposition", "content-range",
        "content-type", "etag", "expires", "last-modified",
    }
    for key, value in upstream.headers.items():
        lowered = key.lower()
        if lowered in fixed or (lowered.startswith("x-") and lowered != "x-accel-redirect"):
            headers[key] = value
    return headers


def _raw_path_is_safe(request, path):
    raw_path = request.scope.get("raw_path", b"")
    raw_lower = raw_path.lower() if isinstance(raw_path, bytes) else b""
    if any(encoded in raw_lower for encoded in (b"%2e", b"%2f", b"%5c", b"%25")):
        return False
    return all(part not in (".", "..") for part in path.replace("\\", "/").split("/"))


def _chat_path_is_safe(request, path):
    """Match the recent chat proxy's slash handling without allowing traversal."""
    raw_path = request.scope.get("raw_path", b"")
    raw_lower = raw_path.lower() if isinstance(raw_path, bytes) else b""
    if any(encoded in raw_lower for encoded in (b"%2e", b"%5c", b"%25")):
        return False
    raw_text = raw_lower.decode("latin-1").replace("\\", "/")
    if any(part in (".", "..") for part in raw_text.split("/")):
        return False
    return all(part not in (".", "..") for part in path.replace("\\", "/").split("/"))


def _proxy_connection(server_id, user):
    try:
        connections = parse_terminal_connections(_connection_json())
    except TerminalPolicyError:
        return None, _proxy_error(503, "Terminal configuration is unavailable")
    connection = next((item for item in connections if item["id"] == server_id), None)
    if connection is None:
        return None, _proxy_error(404, f"Terminal server '{server_id}' not found")
    if getattr(user, "role", None) != "admin" or connection["enabled"] is not True:
        return None, _proxy_error(403, "Access denied")
    return connection, None


async def _proxy_terminal_request(request, connection, path, user_id):
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_PROXY_BYTES:
                return _proxy_error(413, "Terminal request exceeds the transfer limit")
        except ValueError:
            return _proxy_error(400, "Terminal request length is invalid")
    body = await request.body()
    if len(body) > MAX_PROXY_BYTES:
        return _proxy_error(413, "Terminal request exceeds the transfer limit")
    headers = {
        "Authorization": "Bearer " + connection["key"],
        "X-User-Id": str(user_id),
    }
    content_type = request.headers.get("content-type")
    if content_type:
        headers["Content-Type"] = content_type
    target = connection["url"].rstrip("/") + "/" + path.lstrip("/")
    if request.url.query:
        target += "?" + request.url.query
    try:
        async with _fetch_slot(30) as remaining:
            timeout = aiohttp.ClientTimeout(total=remaining, connect=min(5, remaining))
            async with aiohttp.ClientSession(
                timeout=timeout, trust_env=False, auto_decompress=False
            ) as session:
                async with session.request(
                    request.method,
                    target,
                    headers=headers,
                    data=body if request.method in ("POST", "PUT", "PATCH", "DELETE") else None,
                    allow_redirects=False,
                ) as upstream:
                    response_body = b"" if request.method == "HEAD" else await _read_proxy_response(upstream)
                    return Response(
                        content=response_body,
                        status_code=upstream.status,
                        headers=_proxy_response_headers(upstream),
                    )
    except HTTPException as error:
        return _proxy_error(error.status_code, str(error.detail))
    except asyncio.TimeoutError:
        return _proxy_error(504, "Terminal request timed out")
    except aiohttp.ClientError:
        return _proxy_error(503, "Terminal service is unavailable")
    except Exception:
        return _proxy_error(502, "Terminal response is unavailable")


async def _fetch_file(request, spec, filename):
    request_headers = dict(spec.headers)
    request_headers["Accept-Encoding"] = "identity"
    try:
        async with _fetch_slot(15) as remaining:
            timeout = aiohttp.ClientTimeout(total=remaining, connect=min(5, remaining))
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False, auto_decompress=False) as session:
                async with session.request(
                    request.method,
                    spec.url,
                    headers=request_headers,
                    allow_redirects=False,
                ) as upstream:
                    if upstream.status in (401, 403, 404):
                        _http_error(upstream.status, "Terminal file is unavailable.")
                    if upstream.status != 200:
                        _http_error(502, "Terminal file response is unavailable.")
                    encodings = upstream.headers.getall("Content-Encoding", [])
                    if len(encodings) > 1 or (encodings and encodings[0].lower() != "identity"):
                        _http_error(502, "Terminal file response is unavailable.")
                    declared = _declared_length(upstream.headers)
                    if request.method == "HEAD":
                        return Response(
                            content=b"",
                            status_code=200,
                            headers=_download_headers(upstream, filename, declared),
                        )
                    body = await _read_bounded(upstream)
                    return Response(
                        content=body,
                        status_code=200,
                        headers=_download_headers(upstream, filename, len(body)),
                    )
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        _http_error(504, "Terminal file request timed out.")
    except aiohttp.ClientError:
        _http_error(503, "Terminal file service is unavailable.")
    except Exception:
        _http_error(502, "Terminal file response is unavailable.")


@router.get(
    "/",
    response_model=list[dict],
    operation_id="list_terminal_servers_api_v1_terminals__get",
)
async def list_terminal_servers(user=Depends(get_verified_user)):
    try:
        return public_terminal_connections(_connection_json(), user)
    except TerminalPolicyError:
        _http_error(503, "Terminal configuration is unavailable.")


@router.api_route(
    "/{server_id}/files/serve/home/user/requests/{file_path:path}",
    methods=["GET", "HEAD"],
    operation_id=(
        "download_terminal_file_api_v1_terminals__server_id__files_serve_home_user_"
        "requests__file_path__get"
    ),
)
async def download_terminal_file(
    server_id: str, file_path: str, request: Request, user=Depends(get_verified_user)
):
    raw_path = request.scope.get("raw_path", b"")
    raw_lower = raw_path.lower() if isinstance(raw_path, bytes) else b""
    if (b"%2f" in raw_lower or b"%5c" in raw_lower or b"%25" in raw_lower
            or request.url.query):
        _http_error(403, "Terminal file path is unavailable.")
    config = _connection_json()
    try:
        connections = parse_terminal_connections(config)
    except TerminalPolicyError:
        _http_error(503, "Terminal configuration is unavailable.")
    if not any(item["id"] == server_id for item in connections):
        return _proxy_error(404, f"Terminal server '{server_id}' not found")
    path = "files/serve/home/user/requests/" + file_path
    try:
        spec = build_download_request(config, server_id, path, user)
    except TerminalPolicyError:
        return _proxy_error(403, "Access denied")
    filename = unquote(spec.path.rsplit("/", 1)[-1])
    return await _fetch_file(request, spec, filename)


@router.api_route(
    "/{server_id}/browser/view/{sid}/{ticket}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    operation_id=(
        "browser_http_api_v1_terminals__server_id__browser_view__sid___ticket___path__put"
    ),
)
async def browser_http(
    server_id: str, sid: str, ticket: str, path: str, request: Request
):
    # A positive browser-session relay is intentionally unavailable until its
    # token lifecycle can be implemented and qualified without weakening trust.
    return JSONResponse(status_code=401, content={"detail": "Browser session expired"})


@router.api_route(
    "/{server_id}/chats/{chat_id}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    operation_id=(
        "proxy_terminal_api_v1_terminals__server_id__chats__chat_id___path__put"
    ),
    name="proxy_terminal",
)
async def proxy_terminal_chat(
    server_id: str, path: str, request: Request, user=Depends(get_verified_user)
):
    """Proxy a request to the admin terminal server identified by *server_id*."""
    connection, error = _proxy_connection(server_id, user)
    if error is not None:
        return error
    if not _chat_path_is_safe(request, path):
        return _proxy_error(400, "Invalid path")
    return await _proxy_terminal_request(request, connection, path, user.id)


@router.api_route(
    "/{server_id}/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    operation_id="proxy_terminal_api_v1_terminals__server_id___path__put",
)
async def proxy_terminal(
    server_id: str, path: str, request: Request, user=Depends(get_verified_user)
):
    connection, error = _proxy_connection(server_id, user)
    if error is not None:
        return error
    if not _raw_path_is_safe(request, path):
        return _proxy_error(403, "Terminal path is unavailable")
    return await _proxy_terminal_request(request, connection, path, user.id)
