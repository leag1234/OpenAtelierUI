# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Narrow opt-in projection for bounded OpenAI-compatible provider errors."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi.responses import JSONResponse


MAX_PROVIDER_ERROR_BYTES = 1024 * 1024
MAX_PROVIDER_ERROR_FIELD_CHARS = 4096
_SAFE_ERROR_FIELDS = frozenset({"message", "type", "code"})
_GENERIC_ERROR = {"error": {"message": "Provider error response unavailable", "type": "provider_error"}}


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise ValueError("non-finite JSON value")


def _is_bounded_text(value: object) -> bool:
    if not isinstance(value, str) or len(value) > MAX_PROVIDER_ERROR_FIELD_CHARS:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return True


class ProviderErrorResponse(JSONResponse):
    """Marker response that bypasses gateway success post-processing."""


def provider_error_compat_enabled() -> bool:
    return os.environ.get("OPENATELIER_PROVIDER_ERROR_COMPAT") == "1"


async def read_bounded_provider_error_body(reader) -> bytes:
    """Accumulate fragmented reads while retaining at most limit plus one byte."""
    body = bytearray()
    while len(body) <= MAX_PROVIDER_ERROR_BYTES:
        remaining = MAX_PROVIDER_ERROR_BYTES + 1 - len(body)
        chunk = await reader.read(remaining)
        if not chunk:
            break
        body.extend(chunk[:remaining])
    return bytes(body)


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[redacted]")
    return value


def _bearer_value(authorization: str | None) -> str:
    if not isinstance(authorization, str):
        return ""
    scheme, separator, credential = authorization.partition(" ")
    return credential.strip() if separator and scheme.lower() == "bearer" else ""


def build_provider_error_response(
    status_code: int,
    body: bytes,
    selected_key: str | None,
    authorization: str | None,
) -> ProviderErrorResponse:
    """Return only the measured error fields, or a generic bounded fallback."""
    supported_status = type(status_code) is int and 400 <= status_code <= 599
    status = status_code if supported_status else 502
    payload: dict[str, Any] = _GENERIC_ERROR
    if supported_status and isinstance(body, bytes) and len(body) <= MAX_PROVIDER_ERROR_BYTES:
        try:
            decoded = json.loads(
                body.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeError, ValueError, TypeError, RecursionError):
            decoded = None
        if (
            isinstance(decoded, dict)
            and set(decoded) == {"error"}
            and isinstance(decoded["error"], dict)
            and set(decoded["error"]).issubset(_SAFE_ERROR_FIELDS)
            and _is_bounded_text(decoded["error"].get("message"))
            and all(_is_bounded_text(value) for value in decoded["error"].values())
        ):
            secrets = tuple(
                value for value in (selected_key, _bearer_value(authorization))
                if isinstance(value, str) and value
            )
            payload = {
                "error": {
                    name: _redact(value, secrets)
                    for name, value in decoded["error"].items()
                }
            }
    return ProviderErrorResponse(content=payload, status_code=status)
