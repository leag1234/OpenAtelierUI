# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded administrator-initiated source loading for workspace imports."""

import asyncio
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

import aiohttp
from pydantic import BaseModel, HttpUrl


MAX_SOURCE_BYTES = 1024 * 1024


class LoadUrlForm(BaseModel):
    url: HttpUrl


def _validated_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("Invalid URL")
    return value


async def load_source_url(value: str) -> tuple[str, str]:
    """Return bounded UTF-8 source and a stable filename-derived name."""

    url = _validated_url(value)
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, allow_redirects=True, max_redirects=3) as response:
                response.raise_for_status()
                final_url = _validated_url(str(response.url))
                body = await response.content.read(MAX_SOURCE_BYTES + 1)
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        raise ValueError("Unable to load URL") from error
    if len(body) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds 1 MiB")
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("Source is not UTF-8") from error
    filename = PurePosixPath(unquote(urlsplit(final_url).path)).name
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    if not name:
        raise ValueError("URL does not name a source file")
    return content, name
