# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Stateless validator for the three explicitly allowlisted auth settings."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any


MANIFEST_FORMAT = "openatelier-auth-settings/v1"
MAX_MANIFEST_BYTES = 16 * 1024
MAX_DURATION_CHARS = 32
MAX_DURATION_SECONDS = 366 * 24 * 60 * 60
ALLOWED_SETTINGS = {"JWT_EXPIRES_IN", "ENABLE_SIGNUP", "ENABLE_LOGIN_FORM"}
_DURATION_PATTERN = re.compile(r"([1-9][0-9]*)(s|m|h|d|w)", re.ASCII)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class AuthSettingsError(Exception):
    """A safe named refusal without echoing manifest data."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuthSettingsError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise AuthSettingsError("invalid_json_constant")


def _parse_duration(value: Any) -> int:
    if not isinstance(value, str) or not value or len(value) > MAX_DURATION_CHARS:
        raise AuthSettingsError("invalid_expiry_duration")
    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        raise AuthSettingsError("invalid_expiry_duration")
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if seconds <= 0 or seconds > MAX_DURATION_SECONDS:
        raise AuthSettingsError("expiry_duration_out_of_range")
    return seconds


def validate_manifest_bytes(contents: bytes) -> dict[str, Any]:
    """Validate bounded manifest bytes and return a fixed safe projection."""
    if not isinstance(contents, bytes):
        raise AuthSettingsError("invalid_manifest_bytes")
    if len(contents) > MAX_MANIFEST_BYTES:
        raise AuthSettingsError("manifest_too_large")
    try:
        text = contents.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except AuthSettingsError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise AuthSettingsError("invalid_manifest_json") from None

    if not isinstance(document, dict) or set(document) != {"format", "settings"}:
        raise AuthSettingsError("manifest_schema_mismatch")
    if document["format"] != MANIFEST_FORMAT:
        raise AuthSettingsError("unsupported_manifest_format")
    settings = document["settings"]
    if not isinstance(settings, dict) or set(settings) != ALLOWED_SETTINGS:
        raise AuthSettingsError("manifest_settings_mismatch")

    expiry = settings["JWT_EXPIRES_IN"]
    ttl_seconds = _parse_duration(expiry)
    signup = settings["ENABLE_SIGNUP"]
    login_form = settings["ENABLE_LOGIN_FORM"]
    if type(signup) is not bool or type(login_form) is not bool:
        raise AuthSettingsError("invalid_boolean_setting")

    return {
        "environment": {
            "JWT_EXPIRES_IN": expiry,
            "ENABLE_SIGNUP": "true" if signup else "false",
            "ENABLE_LOGIN_FORM": "true" if login_form else "false",
        },
        "ttl_seconds": ttl_seconds,
        "manifest_sha256": hashlib.sha256(contents).hexdigest(),
        "partial": True,
        "cutover": False,
    }


def load_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read one regular, non-symlink file with a strict byte ceiling."""
    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except (OSError, ValueError):
        raise AuthSettingsError("manifest_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode):
        raise AuthSettingsError("manifest_symlink_refused")
    if not stat.S_ISREG(metadata.st_mode):
        raise AuthSettingsError("manifest_not_regular_file")
    if metadata.st_size > MAX_MANIFEST_BYTES:
        raise AuthSettingsError("manifest_too_large")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(candidate, flags)
    except (OSError, ValueError):
        raise AuthSettingsError("manifest_unavailable") from None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise AuthSettingsError("manifest_not_regular_file")
        if opened.st_size > MAX_MANIFEST_BYTES:
            raise AuthSettingsError("manifest_too_large")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            contents = stream.read(MAX_MANIFEST_BYTES + 1)
    except AuthSettingsError:
        raise
    except OSError:
        raise AuthSettingsError("manifest_read_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return validate_manifest_bytes(contents)


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] != "--manifest":
        print(_json_line({"refusal": "invalid_cli_arguments", "partial": True, "cutover": False}))
        return 2
    try:
        result = load_manifest(arguments[1])
    except AuthSettingsError as error:
        print(_json_line({"refusal": error.code, "partial": True, "cutover": False}))
        return 2
    print(_json_line(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
