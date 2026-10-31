# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Opt-in, read-only proxy for one generated terminal attachment."""

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from urllib.parse import quote, unquote

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from open_webui.utils.auth import get_verified_user
from open_webui.utils.terminal_download_policy import (
    TerminalPolicyError,
    build_download_request,
    public_terminal_connections,
    parse_terminal_connections,
)


router = APIRouter()
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
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


@router.get("/", response_model=list[dict])
async def list_terminal_connections(user=Depends(get_verified_user)):
    try:
        return public_terminal_connections(_connection_json(), user)
    except TerminalPolicyError:
        _http_error(503, "Terminal configuration is unavailable.")


@router.api_route(
    "/{server_id}/files/serve/home/user/requests/{file_path:path}",
    methods=["GET", "HEAD"],
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
        parse_terminal_connections(config)
    except TerminalPolicyError:
        _http_error(503, "Terminal configuration is unavailable.")
    path = "files/serve/home/user/requests/" + file_path
    try:
        spec = build_download_request(config, server_id, path, user)
    except TerminalPolicyError:
        _http_error(403, "Terminal file is unavailable.")
    filename = unquote(spec.path.rsplit("/", 1)[-1])
    return await _fetch_file(request, spec, filename)


@router.api_route("/{unmatched:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def refuse_terminal_operation(unmatched: str, request: Request, user=Depends(get_verified_user)):
    if request.method in ("GET", "HEAD"):
        _http_error(403, "Terminal operation is unavailable.")
    _http_error(405, "Terminal operation is unavailable.")
