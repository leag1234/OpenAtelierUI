#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Read-only preview and fresh-target SQLite migration experiment.

This is a bounded synthetic-data prototype. It never executes source SQL or
plugin code, never edits source/template databases, and never activates a
prepared target. Unsupported domains are archived and reported; partial
targets remain ineligible for cutover.
"""

from __future__ import annotations

import argparse
import ast
from datetime import date
import hashlib
import importlib.util
import json
import math
import os
import ipaddress
import re
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import urlsplit

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
_HISTORY_HELPER = BACKEND_ROOT / "open_webui" / "utils" / "imported_history.py"
_HISTORY_SPEC = importlib.util.spec_from_file_location("_open_webui_imported_history", _HISTORY_HELPER)
if _HISTORY_SPEC is None or _HISTORY_SPEC.loader is None:
    raise RuntimeError("could not load the stdlib-only imported-history helper")
_HISTORY_MODULE = importlib.util.module_from_spec(_HISTORY_SPEC)
sys.modules[_HISTORY_SPEC.name] = _HISTORY_MODULE
_HISTORY_SPEC.loader.exec_module(_HISTORY_MODULE)
write_history_sidecar = _HISTORY_MODULE.write_history_sidecar


MAX_INPUT_BYTES = 16 * 1024 * 1024
APP_TABLES = {"auth", "user", "chat", "chat_message", "file", "function", "tool", "model"}
COPYABLE_TABLES = {"auth", "user", "chat", "file", "function", "tool", "model"}
MARKER_TABLES = {"alembic_version", "migratehistory"}
KNOWN_TABLES = {
    "access_grant", "alembic_version", "api_key", "app", "auth", "automation",
    "automation_run", "calendar", "calendar_event", "calendar_event_attendee",
    "channel", "channel_file", "channel_member", "channel_webhook", "chat",
    "chat_file", "chat_message", "chatidtag", "config", "config_old", "document",
    "feedback", "file", "folder", "function", "function_history", "group",
    "group_member", "knowledge", "knowledge_directory", "knowledge_file", "memory",
    "message", "message_reaction", "model", "model_history", "migratehistory",
    "note", "oauth_session", "pinned_note", "prompt", "prompt_history", "shared_chat",
    "skill", "skill_history", "tag", "tool", "tool_history", "user",
}

SOURCE_REQUIRED = {
    "alembic_version": {"version_num"},
    "auth": {"id", "email", "password", "active"},
    "user": {"id", "name", "email", "role", "profile_image_url", "created_at", "updated_at", "last_active_at"},
    "chat": {"id", "user_id", "title", "created_at", "updated_at", "share_id", "archived", "chat", "pinned", "meta", "folder_id"},
    "chat_message": {"id", "chat_id", "user_id", "role", "parent_id", "content"},
    "file": {"id", "user_id", "filename", "meta", "created_at", "hash", "data", "updated_at", "path"},
    "function": {"id", "user_id", "name", "type", "content", "meta", "created_at", "updated_at", "valves", "is_active", "is_global"},
    "tool": {"id", "user_id", "name", "content", "specs", "meta", "valves", "updated_at", "created_at", "version_id"},
    "model": {"id", "user_id", "base_model_id", "name", "params", "meta", "is_active", "updated_at", "created_at", "version_id"},
    "access_grant": {"id", "resource_type", "resource_id", "principal_type", "principal_id", "permission", "created_at"},
}

TARGET_REQUIRED = {
    "alembic_version": {"version_num"},
    "auth": {"id", "email", "password", "active"},
    "user": {"id", "name", "email", "role", "profile_image_url", "api_key", "created_at", "updated_at", "last_active_at", "settings", "info", "oauth_sub"},
    "chat": {"id", "user_id", "title", "created_at", "updated_at", "share_id", "archived", "chat", "pinned", "meta", "folder_id"},
    "file": {"id", "user_id", "filename", "meta", "created_at", "hash", "data", "updated_at", "path", "access_control"},
    "function": {"id", "user_id", "name", "type", "content", "meta", "created_at", "updated_at", "valves", "is_active", "is_global"},
    "tool": {"id", "user_id", "name", "content", "specs", "meta", "valves", "access_control", "updated_at", "created_at"},
    "model": {"id", "user_id", "base_model_id", "name", "params", "meta", "access_control", "is_active", "updated_at", "created_at"},
}

USER_UNSUPPORTED = {
    "username", "bio", "gender", "date_of_birth", "profile_banner_image_url",
    "timezone", "presence_state", "status_emoji", "status_message",
    "status_expires_at", "oauth", "scim", "variables",
}
IMPORTED_USER_PROFILE_FIELDS = {"bio", "gender", "date_of_birth"}
IMPORTED_USER_PROFILE_TYPES = {"bio": "TEXT", "gender": "TEXT", "date_of_birth": "DATE"}
MAX_IMPORTED_USER_BIO_BYTES = 8192
MAX_IMPORTED_USER_GENDER_BYTES = 128
MAX_IMPORTED_API_KEY_ROWS = 1000
MAX_API_KEY_CONFIG_VALUE_BYTES = 16 * 1024
MAX_USER_PERMISSIONS_CONFIG_BYTES = 16 * 1024
API_KEY_COLUMNS = {
    "id", "user_id", "key", "data", "expires_at", "last_used_at", "created_at", "updated_at"
}
API_KEY_PATTERN = re.compile(r"^sk-[0-9a-f]{32}$")
API_KEY_POLICY_CONFIG = {
    "auth.enable_api_keys": True,
    "user.permissions": None,
    "auth.api_key.endpoint_restrictions": False,
    "auth.api_key.allowed_endpoints": "",
}
API_KEY_CONFIG_SOURCE_KEYS = {
    "auth.enable_api_keys",
    "auth.api_key.endpoint_restrictions",
    "auth.api_key.allowed_endpoints",
}
API_KEY_CONFIG_OLD_ALIASES = {
    "ENABLE_API_KEY",
    "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS",
    "API_KEY_ALLOWED_ENDPOINTS",
    "auth.api_key.enable",
}
CHAT_UNSUPPORTED = {"tasks", "summary", "last_read_at", "current_message_id", "variables", "timer_at"}
PRIVATE_CHAT_META_KEY = "_mdk_private_saved_work"
PRIVATE_CHAT_META_VERSION = 1
PRIVATE_CHAT_META_MAX_BYTES = 1024
MAX_NAMED_TOOL_CONFIG_BYTES = 16 * 1024
MAX_NAMED_TOOL_URL_CHARS = 2048
MAX_NAMED_TOOL_PATH_CHARS = 512
MAX_NAMED_TOOL_NAME_CHARS = 256
MAX_NAMED_TOOL_DESCRIPTION_CHARS = 1024
NAMED_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
OLD_CONFIG_SOURCE_SHA256 = "e86c2da3487ddf9d9ee9fe5bafc81f5e96d8bc8a800a5632e3387bec6e57d694"
OLD_CONFIG_DEFAULTS_SHA256 = "7870936571d911d4d4580b924d7b187ac9b5d01121299a1947f5d1ccfc832dd8"
NAMED_TOOL_CONNECTION_KEY = "tool_server.connections"
NAMED_TOOL_CONFIG_PATH = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "config.py"
MESSAGE_UNSUPPORTED = {
    "output", "model_id", "files", "sources", "embeds", "done", "status_history",
    "error", "usage", "created_at", "updated_at", "context_summary", "meta",
}


class MigrationError(Exception):
    def __init__(self, code: str, message: str, report: dict | None = None):
        super().__init__(message)
        self.code = code
        self.report = report or {}


class _DuplicateConfigJSONKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateConfigJSONKey
        result[key] = value
    return result


def _load_pinned_old_defaults() -> tuple[dict, str, str]:
    """Read only the pinned literal defaults; never import the runtime module."""
    path = NAMED_TOOL_CONFIG_PATH
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise MigrationError("named_tool_configuration_defaults_unavailable", "pinned old configuration defaults are unavailable")
        source = path.read_bytes()
    except OSError:
        raise MigrationError("named_tool_configuration_defaults_unavailable", "pinned old configuration defaults are unavailable")
    source_hash = hashlib.sha256(source).hexdigest()
    if source_hash != OLD_CONFIG_SOURCE_SHA256:
        raise MigrationError("named_tool_configuration_defaults_unavailable", "pinned old configuration source does not match")
    try:
        module = ast.parse(source.decode("utf-8"), filename=str(path))
        assignments = [
            node for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG" for target in node.targets)
        ]
        if len(assignments) != 1:
            raise ValueError("default assignment count")
        defaults = ast.literal_eval(assignments[0].value)
        if not isinstance(defaults, dict) or type(defaults.get("version")) is not int:
            raise ValueError("default shape")
    except (UnicodeError, SyntaxError, ValueError, TypeError, RecursionError) as error:
        raise MigrationError("named_tool_configuration_defaults_unavailable", "pinned old configuration defaults could not be read") from error
    canonical = json.dumps(defaults, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    defaults_hash = hashlib.sha256(canonical).hexdigest()
    if defaults_hash != OLD_CONFIG_DEFAULTS_SHA256:
        raise MigrationError("named_tool_configuration_defaults_unavailable", "pinned old configuration defaults do not match")
    return defaults, source_hash, defaults_hash


def _named_tool_target_config_schema(connection: sqlite3.Connection) -> bool:
    if "config" not in _tables(connection):
        return False
    columns = _columns(connection, "config")
    if set(columns) != {"id", "data", "version", "created_at", "updated_at"}:
        return False
    return (
        columns["id"][2].upper() == "INTEGER" and columns["id"][3] == 1 and columns["id"][5] == 1
        and columns["data"][2].upper() == "JSON" and columns["data"][3] == 1
        and columns["version"][2].upper() == "INTEGER" and columns["version"][3] == 1
        and columns["created_at"][2].upper() == "DATETIME" and columns["created_at"][3] == 1 and columns["created_at"][4] == "CURRENT_TIMESTAMP"
        and columns["updated_at"][2].upper() == "DATETIME" and columns["updated_at"][3] == 0 and columns["updated_at"][4] == "CURRENT_TIMESTAMP"
        and _count(connection, "config") == 0
    )


def _valid_named_tool_base_url(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_NAMED_TOOL_URL_CHARS
        or value != value.strip()
        or "?" in value
        or "#" in value
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        return False
    host = parts.hostname
    if not host or not host.isascii() or "%" in host or len(host) > 253:
        return False
    if ":" in host:
        authority = re.fullmatch(r"\[([^\]]+)\](?::([0-9]+))?", parts.netloc)
        if not authority or authority.group(1).lower() != host.lower():
            return False
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
    else:
        authority = re.fullmatch(r"([^:\[\]]+)(?::([0-9]+))?", parts.netloc)
        if not authority or authority.group(1).lower() != host.lower():
            return False
    if ":" not in host and re.fullmatch(r"[0-9.]+", host):
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            return False
    elif ":" not in host:
        labels = host.split(".")
        if not all(
            label
            and len(label) <= 63
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        ):
            return False

    prefix = parts.path
    if prefix in {"", "/"}:
        return True
    if not prefix.startswith("/") or prefix.startswith("//"):
        return False
    relative_prefix = prefix[1:]
    if relative_prefix.endswith("/"):
        relative_prefix = relative_prefix[:-1]
    return _valid_named_tool_path(relative_prefix)


def _valid_named_tool_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_NAMED_TOOL_PATH_CHARS
        or value.startswith("/")
        or "\\" in value
        or "%" in value
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)
    ):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _valid_named_tool_text(value: object, maximum: int, *, nonempty: bool) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and (not nonempty or bool(value.strip()))
        and not any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value)
    )


def _validate_named_tool_connection(connection: sqlite3.Connection, value: object) -> dict:
    """Validate one bounded no-auth named URL connection and exact user/read grant."""
    required_fields = {
        "url", "path", "type", "spec_type", "spec", "auth_type", "key",
        "config", "info",
    }
    optional_fields = {"headers", "forward_cookies"}
    if not isinstance(value, dict) or not required_fields.issubset(value) or set(value) - required_fields - optional_fields:
        raise MigrationError("named_tool_configuration_unsupported_shape", "selected named-tool configuration has unsupported fields")
    info = value.get("info")
    nested = value.get("config")
    if (
        not _valid_named_tool_base_url(value.get("url"))
        or not _valid_named_tool_path(value.get("path"))
        or value.get("type") != "openapi"
        or value.get("spec_type") != "url"
        or value.get("spec") != ""
        or value.get("auth_type") != "none"
        or value.get("key") != ""
        or value.get("headers") not in (None, {})
        or value.get("forward_cookies", False) is not False
        or not isinstance(info, dict)
        or set(info) != {"id", "name", "description"}
        or not isinstance(info.get("id"), str)
        or not info.get("id").isascii()
        or not NAMED_TOOL_ID_RE.fullmatch(info.get("id"))
        or info.get("id").isdecimal()
        or not _valid_named_tool_text(
            info.get("name"), MAX_NAMED_TOOL_NAME_CHARS, nonempty=True
        )
        or not _valid_named_tool_text(
            info.get("description"), MAX_NAMED_TOOL_DESCRIPTION_CHARS, nonempty=False
        )
        or not isinstance(nested, dict)
        or set(nested) != {"enable", "access_grants"}
        or nested.get("enable") is not True
    ):
        raise MigrationError("named_tool_configuration_unsupported_shape", "selected named-tool configuration is outside the supported no-auth profile")
    grants = nested.get("access_grants")
    if not isinstance(grants, list) or len(grants) != 1 or not isinstance(grants[0], dict) or set(grants[0]) != {"principal_type", "principal_id", "permission"}:
        raise MigrationError("named_tool_configuration_unsupported_grant", "selected named-tool grant is outside the measured user/read profile")
    grant = grants[0]
    principal_id = grant.get("principal_id")
    if grant.get("principal_type") != "user" or grant.get("permission") != "read":
        raise MigrationError("named_tool_configuration_unsupported_grant", "selected named-tool grant is outside the measured user/read profile")
    if (
        not isinstance(principal_id, str)
        or not principal_id.strip()
        or principal_id == "*"
    ):
        raise MigrationError("named_tool_configuration_grant_identity_invalid", "selected grant must name an existing ordinary user with read permission")
    identity_rows = connection.execute(
        'SELECT role FROM "user" WHERE id=?', (principal_id,)
    ).fetchall()
    if len(identity_rows) != 1 or identity_rows[0][0] != "user":
        raise MigrationError("named_tool_configuration_grant_identity_invalid", "selected grant must name one existing ordinary user with read permission")
    return value


def _parse_named_tool_connection(connection: sqlite3.Connection) -> list[dict]:
    """Parse an ordered bounded list of independently validated named connections."""
    if "config" not in _tables(connection):
        raise MigrationError("named_tool_configuration_source_schema_mismatch", "selected configuration table is unavailable")
    columns = _columns(connection, "config")
    if set(columns) != {"key", "value", "updated_at"}:
        raise MigrationError("named_tool_configuration_source_schema_mismatch", "selected configuration schema is unsupported")
    rows = [row for row in connection.execute('SELECT value FROM "config" WHERE key=?', (NAMED_TOOL_CONNECTION_KEY,))]
    if len(rows) != 1:
        raise MigrationError("named_tool_configuration_selection_invalid", "exactly one selected named-tool configuration is required")
    raw = rows[0][0]
    try:
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else None
    except UnicodeError:
        raw_bytes = None
    if raw_bytes is None or len(raw_bytes) > MAX_NAMED_TOOL_CONFIG_BYTES:
        raise MigrationError("named_tool_configuration_selection_invalid", "selected named-tool configuration is outside the size or type limit")
    try:
        values = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except (ValueError, TypeError, RecursionError):
        raise MigrationError("named_tool_configuration_selection_invalid", "selected named-tool configuration is invalid JSON") from None
    if not isinstance(values, list) or not 1 <= len(values) <= 8:
        raise MigrationError("named_tool_configuration_unsupported_shape", "between one and eight named-tool connections are required")
    if any(not isinstance(value, dict) for value in values):
        raise MigrationError("named_tool_configuration_unsupported_shape", "each named-tool connection must be an object")
    selected = [_validate_named_tool_connection(connection, value) for value in values]
    ids = [value["info"]["id"] for value in selected]
    if len(ids) != len(set(ids)):
        raise MigrationError("named_tool_configuration_unsupported_shape", "named-tool connection IDs must be unique")
    return selected


def _parse_api_key_configuration(connection: sqlite3.Connection) -> dict[str, object]:
    """Read exactly three measured scalar settings and reject ambiguous aliases."""
    if "config" not in _tables(connection):
        raise MigrationError(
            "api_key_configuration_source_schema_mismatch",
            "selected API-key configuration table is unavailable",
        )
    columns = _columns(connection, "config")
    if set(columns) != {"key", "value", "updated_at"}:
        raise MigrationError(
            "api_key_configuration_source_schema_mismatch",
            "selected API-key configuration schema is unsupported",
        )

    # These flat aliases and parents could supersede the selected values or
    # their nested old-target destinations. Other unrelated config keys stay
    # archive-only and are counted separately.
    guarded_paths = {
        "auth.api_key",
        "auth.enable_api_keys",
        "auth.api_key.enable",
        "auth.api_key.endpoint_restrictions",
        "auth.api_key.allowed_endpoints",
    }
    selected = {}
    for row in connection.execute('SELECT "key","value" FROM "config"'):
        key = row["key"]
        if not isinstance(key, str):
            continue
        if key in API_KEY_CONFIG_OLD_ALIASES:
            raise MigrationError(
                "api_key_configuration_ambiguous_override",
                "configuration contains an unsupported API-key alias or override",
            )
        if key not in API_KEY_CONFIG_SOURCE_KEYS and (
            # Only these exact auth parent objects overlap the selected
            # settings. Other auth.* settings remain archive-only.
            key in {"auth", "auth.api_key"}
            or any(
                key == path or key.startswith(path + ".") or path.startswith(key + ".")
                for path in guarded_paths
            )
        ):
            raise MigrationError(
                "api_key_configuration_ambiguous_override",
                "configuration contains an unsupported API-key parent or dotted override",
            )
        if key in API_KEY_CONFIG_SOURCE_KEYS:
            if key in selected:
                raise MigrationError(
                    "api_key_configuration_duplicate_key",
                    "selected API-key configuration contains duplicate settings",
                )
            raw = row["value"]
            if not isinstance(raw, str):
                raise MigrationError(
                    "api_key_configuration_value_invalid",
                    "selected API-key configuration value has an unsupported type",
                )
            try:
                if len(raw.encode("utf-8", errors="strict")) > MAX_API_KEY_CONFIG_VALUE_BYTES:
                    raise MigrationError(
                        "api_key_configuration_value_invalid",
                        "selected API-key configuration value exceeds the supported bound",
                    )
                selected[key] = json.loads(
                    raw, object_pairs_hook=_reject_duplicate_json_keys
                )
            except MigrationError:
                raise
            except (UnicodeError, ValueError, TypeError, RecursionError):
                raise MigrationError(
                    "api_key_configuration_value_invalid",
                    "selected API-key configuration value is not supported JSON",
                ) from None

    if set(selected) != API_KEY_CONFIG_SOURCE_KEYS:
        raise MigrationError(
            "api_key_configuration_missing_key",
            "all three selected API-key configuration settings are required",
        )
    enable = selected["auth.enable_api_keys"]
    restrictions = selected["auth.api_key.endpoint_restrictions"]
    allowed_endpoints = selected["auth.api_key.allowed_endpoints"]
    if type(enable) is not bool:
        raise MigrationError(
            "api_key_configuration_value_invalid",
            "API-key enablement must be a JSON boolean",
        )
    if restrictions is not False:
        raise MigrationError(
            "api_key_configuration_value_unsupported",
            "only disabled API-key endpoint restrictions are supported",
        )
    if not isinstance(allowed_endpoints, str) or allowed_endpoints != "":
        raise MigrationError(
            "api_key_configuration_value_unsupported",
            "only an empty API-key endpoint allowlist is supported",
        )
    return {
        "enable": enable,
        "endpoint_restrictions": restrictions,
        "allowed_endpoints": allowed_endpoints,
    }


def _parse_user_permissions(connection: sqlite3.Connection) -> dict[str, dict[str, bool]]:
    """Read one exact, bounded nested boolean user-permissions config row."""
    if "config" not in _tables(connection):
        raise MigrationError(
            "user_permissions_source_schema_mismatch",
            "selected user-permissions configuration table is unavailable",
        )
    if set(_columns(connection, "config")) != {"key", "value", "updated_at"}:
        raise MigrationError(
            "user_permissions_source_schema_mismatch",
            "selected user-permissions configuration schema is unsupported",
        )

    selected = []
    for row in connection.execute('SELECT "key","value" FROM "config"'):
        key = row["key"]
        if not isinstance(key, str):
            continue
        folded_key = key.casefold()
        if (
            (key != "user.permissions" and folded_key in {"user.permissions", "user_permissions"})
            or folded_key == "user"
            or folded_key.startswith("user.permissions.")
        ):
            raise MigrationError(
                "user_permissions_ambiguous_override",
                "configuration contains an unsupported user-permissions parent, alias, or dotted override",
            )
        if key == "user.permissions":
            selected.append(row["value"])
    if len(selected) != 1:
        raise MigrationError(
            "user_permissions_selection_invalid",
            "exactly one selected user-permissions setting is required",
        )
    raw = selected[0]
    if not isinstance(raw, str):
        raise MigrationError(
            "user_permissions_value_invalid",
            "selected user-permissions setting must contain JSON text",
        )
    try:
        if len(raw.encode("utf-8", errors="strict")) > MAX_USER_PERMISSIONS_CONFIG_BYTES:
            raise MigrationError(
                "user_permissions_value_invalid",
                "selected user-permissions setting exceeds the supported size bound",
            )
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
    except MigrationError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise MigrationError(
            "user_permissions_value_invalid",
            "selected user-permissions setting is not supported JSON",
        ) from None

    if not isinstance(value, dict) or not value:
        raise MigrationError(
            "user_permissions_value_invalid",
            "selected user-permissions setting must be a nonempty nested boolean object",
        )
    for category, permissions in value.items():
        if not isinstance(category, str) or not category or not isinstance(permissions, dict) or not permissions:
            raise MigrationError(
                "user_permissions_value_invalid",
                "selected user-permissions setting must contain two-level boolean maps",
            )
        for name, enabled in permissions.items():
            if not isinstance(name, str) or not name or type(enabled) is not bool:
                raise MigrationError(
                    "user_permissions_value_invalid",
                    "selected user-permissions setting must contain only boolean leaves",
                )
    features = value.get("features")
    if not isinstance(features, dict) or type(features.get("api_keys")) is not bool:
        raise MigrationError(
            "user_permissions_value_invalid",
            "selected user-permissions setting must include a boolean features.api_keys value",
        )
    return value


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _sidecars(path: Path) -> list[str]:
    return [str(Path(str(path) + suffix)) for suffix in ("-wal", "-shm", "-journal") if Path(str(path) + suffix).exists()]


def _db_path(path: str | Path, *, label: str, max_bytes: int | None = MAX_INPUT_BYTES) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise MigrationError("invalid_database_path", f"{label} must be a regular non-symlink file")
    candidate = candidate.resolve(strict=True)
    sidecars = _sidecars(candidate)
    if sidecars:
        raise MigrationError("ambiguous_sqlite_state", f"{label} has SQLite sidecars; use a stopped, checkpointed snapshot")
    if max_bytes is not None and candidate.stat().st_size > max_bytes:
        raise MigrationError("input_too_large", f"{label} exceeds the {max_bytes}-byte input bound")
    return candidate


def _connect_ro(path: Path) -> sqlite3.Connection:
    uri = path.as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, tuple]:
    escaped = table.replace('"', '""')
    return {row[1]: tuple(row) for row in connection.execute(f'PRAGMA table_info("{escaped}")')}


def _count(connection: sqlite3.Connection, table: str) -> int:
    escaped = table.replace('"', '""')
    return int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    schema = []
    for table in _tables(connection):
        cols = _columns(connection, table)
        schema.append({"table": table, "columns": [
            {"name": name, "type": row[2], "notnull": row[3], "pk": row[5]}
            for name, row in cols.items()
        ]})
    return hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_empty(value) -> bool:
    if value is None or value is False or value == 0 or value == "":
        return True
    if isinstance(value, (bytes, bytearray)):
        return len(value) == 0
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return True
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            return False
        return parsed is None or parsed is False or parsed == 0 or parsed == "" or parsed == {} or parsed == []
    return False


def _valid_nullable_profile_string(value: object, maximum_bytes: int) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        return len(value.encode("utf-8", errors="strict")) <= maximum_bytes
    except UnicodeError:
        return False


def _valid_nullable_profile_date(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _resource_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def _metrics(started: float, input_bytes: int) -> dict:
    return {
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "process_cpu_seconds_since_process_start": round(time.process_time(), 6),
        "process_peak_rss_bytes": _resource_bytes(),
        "process_peak_rss_platform_units": "bytes" if sys.platform == "darwin" else "KiB converted to bytes",
        "input_bytes": input_bytes,
        "input_limit_bytes": MAX_INPUT_BYTES,
    }


def _block(report: dict, code: str, count: int, *, fatal: bool = False, detail: str | None = None) -> None:
    item = {"code": code, "count": int(count), "fatal": fatal}
    if detail:
        item["detail"] = detail
    report["blockers"].append(item)
    if fatal:
        report["fatal"] = True


def _api_key_config_is_eligible(connection: sqlite3.Connection) -> bool:
    if "config" not in _tables(connection):
        return False
    columns = set(_columns(connection, "config"))
    if not {"key", "value"}.issubset(columns):
        return False
    wanted = set(API_KEY_POLICY_CONFIG)
    rows = []
    for row in connection.execute('SELECT "key","value" FROM "config"'):
        key = row["key"]
        if isinstance(key, str) and key.startswith("user.permissions."):
            # Refuse dotted overrides instead of guessing how the current
            # config loader resolves them against its nested parent object.
            return False
        if key in wanted:
            rows.append((key, row["value"]))
    values = {}
    for key, raw in rows:
        if key in values or not isinstance(raw, str):
            return False
        try:
            values[key] = json.loads(raw, object_pairs_hook=_reject_duplicate_json_keys)
        except (ValueError, TypeError, RecursionError):
            return False
    if set(values) != wanted:
        return False
    for key, expected in API_KEY_POLICY_CONFIG.items():
        value = values[key]
        if key == "user.permissions":
            if not isinstance(value, dict):
                return False
            features = value.get("features")
            if not isinstance(features, dict) or features.get("api_keys") is not True:
                return False
        elif value != expected or type(value) is not type(expected):
            return False
    return True


def _empty_api_key_data(value: object) -> bool:
    if value is None or value == "":
        return True
    if not isinstance(value, str):
        return False
    try:
        decoded = json.loads(value, object_pairs_hook=_reject_duplicate_json_keys)
    except (ValueError, TypeError, RecursionError):
        return False
    return decoded is None or decoded == {}


def _validate_api_key_import(
    connection: sqlite3.Connection,
    report: dict,
    tables: set[str],
    valid_required: dict[str, bool],
) -> dict[str, str]:
    """Validate the one measured legacy key shape without exposing row values."""
    report["api_key_import"] = {
        "opt_in": True,
        "eligible": False,
        "selected_key_count": 0,
        "key_record_metadata_preserved": False,
    }
    if "api_key" not in tables or _count(connection, "api_key") == 0:
        report["api_key_import"]["eligible"] = True
        return {}
    count = _count(connection, "api_key")
    if count > MAX_IMPORTED_API_KEY_ROWS:
        _block(report, "api_key_resource_cap_exceeded", count, fatal=True)
        return {}
    if set(_columns(connection, "api_key")) != API_KEY_COLUMNS:
        _block(report, "api_key_source_schema_unsupported", 1, fatal=True)
        return {}
    if not valid_required.get("auth") or not valid_required.get("user"):
        _block(report, "api_key_owner_schema_unavailable", 1, fatal=True)
        return {}
    if not _api_key_config_is_eligible(connection):
        _block(report, "api_key_source_policy_unsupported", 1, fatal=True)
        return {}

    users = {
        row["id"]: row["role"]
        for row in connection.execute('SELECT id,role FROM "user"')
    }
    auth_active = {
        row["id"]: row["active"]
        for row in connection.execute('SELECT id,active FROM "auth"')
    }
    rows = [dict(row) for row in connection.execute('SELECT * FROM "api_key" ORDER BY id')]
    ids, owners, keys = set(), set(), set()
    mapped = {}
    invalid = False
    for row in rows:
        identity, owner, key = row["id"], row["user_id"], row["key"]
        if (
            not isinstance(identity, str) or not identity or len(identity) > 255
            or identity in ids
            or not isinstance(owner, str) or not owner or len(owner) > 255
            or owner in owners
            or not isinstance(key, str) or API_KEY_PATTERN.fullmatch(key) is None
            or key in keys
            or not _empty_api_key_data(row["data"])
            or row["expires_at"] is not None
            or (row["last_used_at"] is not None and (type(row["last_used_at"]) is not int or row["last_used_at"] < 0))
            or type(row["created_at"]) is not int or row["created_at"] < 0
            or type(row["updated_at"]) is not int or row["updated_at"] < 0
        ):
            invalid = True
            continue
        if (
            owner not in users or owner not in auth_active
            or users[owner] not in {"user", "admin"}
            or auth_active[owner] != 1
        ):
            invalid = True
            continue
        ids.add(identity)
        owners.add(owner)
        keys.add(key)
        mapped[owner] = key

    if invalid or len(mapped) != count:
        _block(report, "api_key_record_unsupported", count, fatal=True)
        return {}

    # A nonempty group permission object is outside this key-sharing profile.
    # The existing group importer also rejects it; this explicit guard makes
    # the API-key eligibility rule independent of that later validation order.
    if "group" in tables and _count(connection, "group"):
        group_columns = set(_columns(connection, "group"))
        if "permissions" not in group_columns:
            _block(report, "api_key_group_permissions_unsupported", 1, fatal=True)
            return {}
        for row in connection.execute('SELECT permissions FROM "group"'):
            try:
                permissions = json.loads(
                    row[0], object_pairs_hook=_reject_duplicate_json_keys
                )
            except (ValueError, TypeError, RecursionError):
                _block(report, "api_key_group_permissions_unsupported", 1, fatal=True)
                return {}
            if not isinstance(permissions, dict) or permissions:
                _block(report, "api_key_group_permissions_unsupported", 1, fatal=True)
                return {}

    report["api_key_import"].update({
        "eligible": True,
        "selected_key_count": len(mapped),
    })
    _block(
        report,
        "api_key_record_metadata_not_preserved",
        count,
        detail="key-record IDs and usage/timestamp metadata remain in the exact source archive; the original key is copied only to the same legacy user field",
    )
    report["preserved_domains"].append("validated opaque API key bytes on the matching legacy user field")
    return mapped


def _source_file_rows(connection: sqlite3.Connection, uploads_root: str | Path, report: dict) -> list[dict]:
    root = Path(uploads_root)
    if root.is_symlink() or not root.is_dir():
        _block(report, "uploads_root_missing_or_unsafe", 1, fatal=True)
        return []
    root = root.resolve(strict=True)
    rows = [dict(row) for row in connection.execute('SELECT * FROM "file" ORDER BY id')]
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        _block(report, "duplicate_file_ids", len(ids) - len(set(ids)), fatal=True)
    users = (
        {row[0] for row in connection.execute('SELECT id FROM "user"')}
        if "user" in _tables(connection) and "id" in _columns(connection, "user")
        else set()
    )
    resolved = []
    total_bytes = 0
    for row in rows:
        file_id = row.get("id")
        filename = row.get("filename")
        stored_path = row.get("path")
        if not isinstance(filename, str) or not filename or filename in {".", ".."} or "/" in filename or "\\" in filename or "\x00" in filename:
            _block(report, "unsafe_file_name", 1, fatal=True)
            continue
        if not isinstance(stored_path, str) or "\x00" in stored_path or "\\" in stored_path:
            _block(report, "unsafe_file_path", 1, fatal=True)
            continue
        stored_parts = PurePosixPath(stored_path).parts
        basename = PurePosixPath(stored_path).name
        expected = f"{file_id}_{filename}"
        if ".." in stored_parts or basename != expected:
            _block(report, "unsafe_file_path", 1, fatal=True)
            continue
        candidate = root / basename
        if candidate.is_symlink() or not candidate.is_file():
            _block(report, "uploaded_file_missing_or_unsafe", 1, fatal=True)
            continue
        resolved_path = candidate.resolve(strict=True)
        if resolved_path.parent != root:
            _block(report, "uploaded_file_escapes_root", 1, fatal=True)
            continue
        size = candidate.stat().st_size
        total_bytes += size
        if total_bytes > MAX_INPUT_BYTES:
            _block(report, "dataset_too_large", total_bytes, fatal=True)
            continue
        meta = row.get("meta")
        try:
            meta_value = json.loads(meta) if isinstance(meta, str) else (meta or {})
        except (ValueError, TypeError):
            _block(report, "invalid_file_metadata", 1, fatal=True)
            continue
        expected_hash = meta_value.get("file_hash") if isinstance(meta_value, dict) else None
        expected_size = meta_value.get("size") if isinstance(meta_value, dict) else None
        actual_hash = _digest(candidate)
        if expected_hash and expected_hash != actual_hash:
            _block(report, "uploaded_file_hash_mismatch", 1, fatal=True)
        if expected_size is not None and expected_size != size:
            _block(report, "uploaded_file_size_mismatch", 1, fatal=True)
        if row.get("user_id") not in users:
            _block(report, "file_owner_missing", 1, fatal=True)
        if not _is_empty(row.get("data")):
            _block(report, "inline_file_payload_unsupported", 1, detail="bytes are archived but not mapped to the old file model")
        resolved.append({"id": file_id, "filename": filename, "source": str(candidate), "bytes": size, "sha256": actual_hash})
    report["upload_bytes"] = total_bytes
    return resolved


def _validate_chat(connection: sqlite3.Connection, report: dict, *, include_private_chat_metadata: bool = False) -> None:
    tables = set(_tables(connection))
    if "chat" not in tables or "chat_message" not in tables:
        return
    if not SOURCE_REQUIRED["chat"].issubset(_columns(connection, "chat")) or not SOURCE_REQUIRED["chat_message"].issubset(_columns(connection, "chat_message")):
        return
    if _count(connection, "chat") == 0:
        return
    rows = [dict(row) for row in connection.execute('SELECT * FROM "chat" ORDER BY id')]
    owners = {row["id"]: row["user_id"] for row in rows}
    chat_ids = set(owners)
    for message in connection.execute('SELECT id,chat_id,user_id FROM "chat_message"'):
        if message["chat_id"] not in chat_ids:
            _block(report, "orphan_normalized_message", 1, fatal=True)
        elif owners[message["chat_id"]] != message["user_id"]:
            _block(report, "normalized_message_owner_mismatch", 1, fatal=True)
    seen = set()
    for chat in rows:
        chat_id = chat["id"]
        if chat_id in seen:
            _block(report, "duplicate_chat_id", 1, fatal=True)
        seen.add(chat_id)
        try:
            payload = json.loads(chat["chat"]) if isinstance(chat["chat"], str) else chat["chat"]
        except (ValueError, TypeError):
            _block(report, "invalid_chat_json", 1, fatal=True)
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("history"), dict) or not isinstance(payload["history"].get("messages"), dict):
            _block(report, "unsupported_chat_shape", 1, fatal=True)
            continue
        history = payload["history"]
        nodes = history["messages"]
        current = history.get("currentId")
        if current is None or current not in nodes:
            _block(report, "invalid_selected_chat_branch", 1, fatal=True)
        if not _is_empty(chat.get("current_message_id")) and chat.get("current_message_id") != current:
            _block(report, "chat_selected_branch_mismatch", 1, fatal=True)
        parent_edges = {}
        invalid_graph = False
        for node_id, node in nodes.items():
            if not isinstance(node, dict) or node.get("id") != node_id:
                invalid_graph = True
                break
            parent_edges[node_id] = node.get("parentId")
        if not invalid_graph:
            for node_id, node in nodes.items():
                parent = node.get("parentId")
                if parent is not None and (parent not in nodes or node_id not in nodes[parent].get("childrenIds", [])):
                    invalid_graph = True
                    break
                for child in node.get("childrenIds", []):
                    if child not in nodes or nodes[child].get("parentId") != node_id:
                        invalid_graph = True
                        break
        if not invalid_graph:
            for node_id in nodes:
                path = set()
                cursor = node_id
                while cursor is not None:
                    if cursor in path:
                        invalid_graph = True
                        break
                    path.add(cursor)
                    cursor = nodes[cursor].get("parentId")
                if invalid_graph:
                    break
        if invalid_graph:
            _block(report, "inconsistent_chat_graph", 1, fatal=True)
            continue
        normalized = [dict(row) for row in connection.execute('SELECT * FROM "chat_message" WHERE chat_id=? ORDER BY id', (chat_id,))]
        normalized_ids = {}
        for message in normalized:
            prefix = chat_id + "-"
            if not isinstance(message.get("id"), str) or not message["id"].startswith(prefix):
                _block(report, "unknown_normalized_message_id", 1, fatal=True)
                continue
            node_id = message["id"][len(prefix):]
            if node_id in normalized_ids:
                _block(report, "duplicate_normalized_message", 1, fatal=True)
            normalized_ids[node_id] = message
        if set(normalized_ids) != set(nodes):
            _block(report, "chat_message_set_mismatch", abs(len(normalized_ids) - len(nodes)) or 1, fatal=True)
        for node_id, node in nodes.items():
            message = normalized_ids.get(node_id)
            if not message:
                continue
            try:
                content = json.loads(message["content"]) if isinstance(message.get("content"), str) else message.get("content")
            except (ValueError, TypeError):
                _block(report, "invalid_normalized_message_content", 1, fatal=True)
                continue
            if message.get("role") != node.get("role") or message.get("parent_id") != node.get("parentId") or content != node.get("content"):
                _block(report, "chat_json_normalized_message_mismatch", 1, fatal=True)
            if message.get("role") not in {"user", "assistant", "system", "tool", "function"}:
                _block(report, "unsupported_message_role", 1, fatal=True)
            for field in MESSAGE_UNSUPPORTED:
                if field in message and not _is_empty(message[field]):
                    _block(report, "unsupported_message_field_" + field, 1)
        for item in payload.get("messages", []):
            if not isinstance(item, dict) or item.get("id") not in nodes:
                _block(report, "chat_preview_message_mismatch", 1, fatal=True)
                break
            node = nodes[item["id"]]
            if any(k in item and item[k] != node.get(k) for k in ("role", "content")):
                _block(report, "chat_preview_message_mismatch", 1, fatal=True)
        for field in CHAT_UNSUPPORTED:
            if field in chat and not _is_empty(chat[field]):
                # current_message_id is supported through chat.history.currentId.
                if field == "current_message_id":
                    continue
                if include_private_chat_metadata and field == "last_read_at":
                    continue
                _block(report, "unsupported_chat_field_" + field, 1, fatal=include_private_chat_metadata)


def _private_chat_metadata_overrides(connection: sqlite3.Connection, report: dict) -> dict[str, dict[str, str]]:
    """Prepare only same-owner private chat JSON and the namespaced read marker."""
    profiles = {}
    for row in connection.execute('SELECT id,name FROM "user"'):
        if isinstance(row[0], str) and row[0] and isinstance(row[1], str) and row[1].strip():
            profiles[row[0]] = {"id": row[0], "name": row[1]}
    overrides: dict[str, dict[str, str]] = {}
    for chat in (dict(row) for row in connection.execute('SELECT * FROM "chat" ORDER BY id')):
        chat_id = chat.get("id")
        owner_id = chat.get("user_id")
        if chat.get("share_id") is not None or chat.get("folder_id") is not None:
            _block(report, "private_chat_metadata_requires_unshared_unfoldered_chat", 1, fatal=True)
            continue
        if not isinstance(owner_id, str) or owner_id not in profiles:
            _block(report, "private_chat_metadata_owner_profile_missing", 1, fatal=True)
            continue
        try:
            payload = json.loads(chat["chat"]) if isinstance(chat.get("chat"), str) else chat.get("chat")
            metadata = json.loads(chat["meta"]) if isinstance(chat.get("meta"), str) else chat.get("meta")
        except (ValueError, TypeError):
            _block(report, "private_chat_metadata_invalid_json", 1, fatal=True)
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("history"), dict) or not isinstance(payload["history"].get("messages"), dict):
            _block(report, "private_chat_metadata_unsupported_chat_shape", 1, fatal=True)
            continue
        if not isinstance(metadata, dict):
            _block(report, "private_chat_metadata_invalid_source_meta", 1, fatal=True)
            continue
        if PRIVATE_CHAT_META_KEY in metadata:
            _block(report, "private_chat_metadata_reserved_key_collision", 1, fatal=True)
            continue
        read_at = chat.get("last_read_at")
        if read_at is not None and (type(read_at) is not int or read_at < 0):
            _block(report, "private_chat_metadata_invalid_last_read_at", 1, fatal=True)
            continue
        nodes = payload["history"]["messages"]
        normalized = [dict(row) for row in connection.execute('SELECT * FROM "chat_message" WHERE chat_id=? ORDER BY id', (chat_id,))]
        if len(normalized) != len(nodes):
            _block(report, "private_chat_metadata_author_set_mismatch", 1, fatal=True)
            continue
        by_node = {}
        author_invalid = False
        prefix = chat_id + "-" if isinstance(chat_id, str) else ""
        for message in normalized:
            identity = message.get("id")
            author = message.get("user_id")
            if (not isinstance(identity, str) or not identity.startswith(prefix) or identity == prefix
                    or not isinstance(author, str) or author != owner_id or author not in profiles):
                author_invalid = True
                break
            node_id = identity[len(prefix):]
            if node_id in by_node or node_id not in nodes:
                author_invalid = True
                break
            by_node[node_id] = message
        if author_invalid or set(by_node) != set(nodes):
            _block(report, "private_chat_metadata_unknown_or_mixed_author", 1, fatal=True)
            continue
        unsupported = False
        for field in ("tasks", "summary", "variables", "timer_at"):
            if field in chat and not _is_empty(chat[field]):
                _block(report, "private_chat_metadata_unsupported_state_" + field, 1, fatal=True)
                unsupported = True
        if unsupported:
            continue
        for node_id, node in nodes.items():
            message = by_node[node_id]
            if message.get("role") != node.get("role"):
                _block(report, "private_chat_metadata_role_mismatch", 1, fatal=True)
                author_invalid = True
                break
            if node.get("user_id") is not None and node.get("user_id") != owner_id:
                _block(report, "private_chat_metadata_json_author_mismatch", 1, fatal=True)
                author_invalid = True
                break
            if node.get("role") == "user" and isinstance(node.get("user"), dict):
                stated_profile_id = node["user"].get("id")
                if stated_profile_id is not None and stated_profile_id != owner_id:
                    _block(report, "private_chat_metadata_json_profile_mismatch", 1, fatal=True)
                    author_invalid = True
                    break
            if node.get("role") == "user":
                node["user_id"] = owner_id
                node["user"] = dict(profiles[owner_id])
            else:
                node.pop("user", None)
        if author_invalid:
            continue
        flat = payload.get("messages", [])
        if not isinstance(flat, list):
            _block(report, "private_chat_metadata_flat_messages_invalid", 1, fatal=True)
            continue
        for item in flat:
            if not isinstance(item, dict) or item.get("id") not in nodes:
                _block(report, "private_chat_metadata_flat_messages_invalid", 1, fatal=True)
                author_invalid = True
                break
            source_node = nodes[item["id"]]
            if item.get("user_id") is not None and item.get("user_id") != owner_id:
                _block(report, "private_chat_metadata_flat_author_mismatch", 1, fatal=True)
                author_invalid = True
                break
            if source_node.get("role") == "user" and isinstance(item.get("user"), dict):
                stated_profile_id = item["user"].get("id")
                if stated_profile_id is not None and stated_profile_id != owner_id:
                    _block(report, "private_chat_metadata_flat_profile_mismatch", 1, fatal=True)
                    author_invalid = True
                    break
            if source_node.get("role") == "user":
                item["user_id"] = owner_id
                item["user"] = dict(profiles[owner_id])
            else:
                item.pop("user", None)
        if author_invalid:
            continue
        marker = {"version": PRIVATE_CHAT_META_VERSION, "last_read_at": read_at}
        if len(json.dumps(marker, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > PRIVATE_CHAT_META_MAX_BYTES:
            _block(report, "private_chat_metadata_marker_too_large", 1, fatal=True)
            continue
        metadata[PRIVATE_CHAT_META_KEY] = marker
        overrides[chat_id] = {
            "chat": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "meta": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        }
    if not report["fatal"]:
        report["private_chat_metadata"] = {
            "enabled": True,
            "converted_chat_count": len(overrides),
            "chat_ids": sorted(overrides),
            "marker_key": PRIVATE_CHAT_META_KEY,
            "marker_version": PRIVATE_CHAT_META_VERSION,
        }
    return overrides


def _json_value(value, expected_type, code: str, report: dict):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        _block(report, code, 1, fatal=True)
        return None
    if not isinstance(parsed, expected_type):
        _block(report, code, 1, fatal=True)
        return None
    return parsed


OLD_GROUP_COLUMNS = {
    "id", "user_id", "name", "description", "data", "meta", "permissions",
    "user_ids", "created_at", "updated_at",
}
CURRENT_GROUP_COLUMNS = {
    "id", "user_id", "name", "description", "data", "meta", "permissions",
    "created_at", "updated_at", "parent_group_id",
}
CURRENT_GROUP_MEMBER_COLUMNS = {"id", "group_id", "user_id", "created_at", "updated_at"}


def _validate_old_group_target(template: Path, report: dict) -> None:
    connection = _connect_ro(template)
    try:
        tables = set(_tables(connection))
        if "group" not in tables:
            _block(report, "old_template_group_table_missing", 1, fatal=True)
            return
        if set(_columns(connection, "group")) != OLD_GROUP_COLUMNS:
            _block(report, "old_template_group_schema_mismatch", 1, fatal=True)
            return
        if _count(connection, "group"):
            _block(report, "old_template_not_empty", _count(connection, "group"), fatal=True)
    finally:
        connection.close()


def _validate_group_domains(
    connection: sqlite3.Connection, report: dict, user_ids: set[str], template: Path
) -> dict[str, list[str]]:
    """Validate the measured flat group profile and build legacy user_ids arrays."""
    tables = set(_tables(connection))
    group_count = _count(connection, "group") if "group" in tables else 0
    member_count = _count(connection, "group_member") if "group_member" in tables else 0
    if not group_count and not member_count:
        return {}
    if group_count > 1000 or member_count > 10000:
        _block(
            report,
            "group_import_resource_cap_exceeded",
            max(group_count, member_count),
            fatal=True,
            detail="bounded profile supports at most 1000 groups and 10000 memberships",
        )
        return {}
    if "group" not in tables or "group_member" not in tables:
        _block(report, "group_membership_schema_missing", 1, fatal=True)
        return {}
    if set(_columns(connection, "group")) != CURRENT_GROUP_COLUMNS:
        _block(report, "source_schema_mismatch_group", 1, fatal=True)
        return {}
    if set(_columns(connection, "group_member")) != CURRENT_GROUP_MEMBER_COLUMNS:
        _block(report, "source_schema_mismatch_group_member", 1, fatal=True)
        return {}
    _validate_old_group_target(template, report)

    groups = [dict(row) for row in connection.execute('SELECT * FROM "group" ORDER BY id')]
    group_ids = [row.get("id") for row in groups]
    if any(not isinstance(value, str) or not value.strip() for value in group_ids):
        _block(report, "invalid_group_id", 1, fatal=True)
    if len(group_ids) != len(set(group_ids)):
        _block(report, "duplicate_group_id", len(group_ids) - len(set(group_ids)), fatal=True)
    known_groups = {value for value in group_ids if isinstance(value, str) and value.strip()}
    for row in groups:
        owner_id = row.get("user_id")
        if not isinstance(owner_id, str) or owner_id not in user_ids:
            _block(report, "group_owner_missing", 1, fatal=True)
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            _block(report, "invalid_group_name", 1, fatal=True)
        if not isinstance(row.get("description"), str):
            _block(report, "invalid_group_description", 1, fatal=True)
        for field in ("created_at", "updated_at"):
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):
                _block(report, "invalid_group_timestamp", 1, fatal=True)
        if row.get("parent_group_id") is not None:
            _block(report, "unsupported_group_hierarchy", 1, fatal=True)
        data = _json_value(row.get("data"), dict, "invalid_group_data", report)
        if data != {"config": {"share": "members"}}:
            _block(report, "unsupported_group_data_profile", 1, fatal=True)
        meta = row.get("meta")
        if meta is not None:
            _block(report, "unsupported_group_meta_profile", 1, fatal=True)
        permissions = _json_value(row.get("permissions"), dict, "invalid_group_permissions", report)
        if permissions != {}:
            _block(report, "unsupported_group_permissions", 1, fatal=True)

    members = [dict(row) for row in connection.execute('SELECT * FROM "group_member" ORDER BY group_id,created_at,id')]
    member_ids = [row.get("id") for row in members]
    if any(not isinstance(value, str) or not value.strip() for value in member_ids):
        _block(report, "invalid_group_member_id", 1, fatal=True)
    if len(member_ids) != len(set(member_ids)):
        _block(report, "duplicate_group_member_id", len(member_ids) - len(set(member_ids)), fatal=True)
    membership_pairs = set()
    members_by_group = {group_id: [] for group_id in known_groups}
    for row in members:
        group_id, user_id = row.get("group_id"), row.get("user_id")
        if group_id not in known_groups:
            _block(report, "dangling_group_membership", 1, fatal=True)
        if not isinstance(user_id, str) or user_id not in user_ids:
            _block(report, "group_member_user_missing", 1, fatal=True)
        pair = (group_id, user_id)
        if pair in membership_pairs:
            _block(report, "duplicate_group_membership", 1, fatal=True)
        membership_pairs.add(pair)
        for field in ("created_at", "updated_at"):
            if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):
                _block(report, "invalid_group_member_timestamp", 1, fatal=True)
        if group_id in members_by_group and isinstance(user_id, str) and user_id in user_ids:
            members_by_group[group_id].append(user_id)

    for values in members_by_group.values():
        if len(values) != len(set(values)):
            _block(report, "duplicate_group_membership", 1, fatal=True)

    report["group_import"] = {
        "profile": "flat_default_members",
        "groups": len(groups),
        "memberships": len(members),
        "membership_ids_and_timestamps": "retained in exact source archive; old target stores user_ids only",
    }
    if members:
        _block(report, "group_membership_identity_and_time_not_preserved", len(members),
               detail="old group JSON retains exact member IDs, not current group_member row IDs or timestamps")
    report["preserved_domains"].append("validated flat group data and direct member IDs")
    return members_by_group


def _validate_object_domains(
    connection: sqlite3.Connection, report: dict, user_ids: set[str], group_ids: set[str] | None = None
) -> dict[tuple[str, str], object]:
    """Validate active object rows and map only measured read ACLs to old JSON ACLs."""
    tables = set(_tables(connection))
    acl: dict[tuple[str, str], object] = {}
    objects: dict[str, dict[str, dict]] = {"tool": {}, "model": {}}
    expected_source = {
        "tool": SOURCE_REQUIRED["tool"] | {"specs", "meta", "valves"},
        "model": SOURCE_REQUIRED["model"] | {"params", "meta"},
    }
    for kind, required in expected_source.items():
        if kind not in tables:
            continue
        cols = set(_columns(connection, kind))
        if not required.issubset(cols) or cols - (required | {"version_id"}):
            _block(report, "source_schema_mismatch_" + kind, 1, fatal=True)
            continue
        rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{kind}" ORDER BY id')]
        ids = [row.get("id") for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in ids):
            _block(report, "invalid_object_id_" + kind, 1, fatal=True)
        if len(ids) != len(set(ids)):
            _block(report, "duplicate_object_ids_" + kind, len(ids) - len(set(ids)), fatal=True)
        for row in rows:
            identity = row.get("id")
            if not isinstance(identity, str) or not identity.strip():
                continue
            objects[kind][identity] = row
            owner = row.get("user_id")
            if not isinstance(owner, str) or owner not in user_ids:
                _block(report, "object_owner_missing_" + kind, 1, fatal=True)
            version_id = row.get("version_id")
            if version_id is not None and (not isinstance(version_id, str) or not version_id.strip()):
                _block(report, "invalid_version_id_" + kind, 1, fatal=True)
            for field in ("name",):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    _block(report, "invalid_object_" + kind + "_" + field, 1, fatal=True)
            for field in ("created_at", "updated_at"):
                if not isinstance(row.get(field), int) or isinstance(row.get(field), bool):
                    _block(report, "invalid_object_" + kind + "_timestamp", 1, fatal=True)
            if kind == "tool":
                if not isinstance(row.get("content"), str):
                    _block(report, "invalid_tool_content", 1, fatal=True)
                _json_value(row.get("specs"), list, "invalid_tool_specs", report)
                meta = _json_value(row.get("meta"), dict, "invalid_tool_meta", report)
                valves = row.get("valves")
                if valves is not None:
                    _json_value(valves, dict, "invalid_tool_valves", report)
                    _block(report, "tool_valves_not_behaviorally_preserved", 1, detail="the old response model omits tool valves")
                if isinstance(meta, dict) and set(meta) - {"description", "manifest"}:
                    _block(report, "tool_metadata_fields_not_behaviorally_preserved", len(set(meta) - {"description", "manifest"}), detail="stored metadata keys outside the old ToolMeta response are not exposed")
            else:
                params = _json_value(row.get("params"), dict, "invalid_model_params", report)
                meta = _json_value(row.get("meta"), dict, "invalid_model_meta", report)
                if row.get("base_model_id") is not None and not isinstance(row.get("base_model_id"), str):
                    _block(report, "invalid_model_base_id", 1, fatal=True)
                if row.get("is_active") not in (0, 1, False, True):
                    _block(report, "invalid_model_active_flag", 1, fatal=True)
                # Old ModelParams and ModelMeta explicitly retain extra JSON keys.
                del params, meta

    history_specs = {
        "tool_history": ("tool", "tool_id"),
        "model_history": ("model", "model_id"),
    }
    history_rows: dict[str, dict[str, dict]] = {}
    for table, (kind, owner_column) in history_specs.items():
        if table not in tables:
            history_rows[kind] = {}
            if any(row.get("version_id") not in (None, "") for row in objects[kind].values()):
                _block(report, "missing_history_table_for_version_" + kind, 1, fatal=True)
            continue
        required = {"id", owner_column, "parent_id", "snapshot", "user_id", "commit_message", "created_at"}
        cols = set(_columns(connection, table))
        if not required.issubset(cols) or cols - required:
            _block(report, "source_schema_mismatch_" + table, 1, fatal=True)
            continue
        rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id')]
        ids = [row.get("id") for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in ids) or len(ids) != len(set(ids)):
            _block(report, "invalid_or_duplicate_history_ids_" + kind, 1, fatal=True)
        history_rows[kind] = {row["id"]: row for row in rows if isinstance(row.get("id"), str)}
        for row in rows:
            resource = objects[kind].get(row.get(owner_column))
            if resource is None:
                _block(report, "history_resource_missing_" + kind, 1, fatal=True)
            author_id = row.get("user_id")
            if not isinstance(author_id, str) or not author_id.strip():
                _block(report, "invalid_history_author_" + kind, 1, fatal=True)
            elif author_id not in user_ids:
                _block(report, "unknown_history_author_" + kind, 1, fatal=True)
            if not isinstance(row.get("created_at"), int) or isinstance(row.get("created_at"), bool):
                _block(report, "invalid_history_timestamp_" + kind, 1, fatal=True)
            snapshot = _json_value(row.get("snapshot"), dict, "invalid_history_snapshot_" + kind, report)
            if isinstance(snapshot, dict):
                required_snapshot = {"name", "content", "meta"} if kind == "tool" else {"name", "base_model_id", "params", "meta"}
                if not required_snapshot.issubset(snapshot):
                    _block(report, "invalid_history_snapshot_" + kind, 1, fatal=True)
                for field in required_snapshot:
                    expected_type = dict if field in {"meta", "params"} else str
                    if kind == "model" and field == "base_model_id":
                        if field not in snapshot or (snapshot[field] is not None and not isinstance(snapshot[field], str)):
                            _block(report, "invalid_history_snapshot_" + kind, 1, fatal=True)
                        continue
                    if not isinstance(snapshot.get(field), expected_type):
                        _block(report, "invalid_history_snapshot_" + kind, 1, fatal=True)
                for json_field, json_type in (("content", str), ("specs", list), ("meta", dict), ("valves", (dict, type(None)))):
                    if kind == "tool" and json_field in snapshot and not isinstance(snapshot[json_field], json_type):
                        _block(report, "invalid_history_snapshot_" + kind, 1, fatal=True)
                for json_field, json_type in (("params", dict), ("meta", dict)):
                    if kind == "model" and json_field in snapshot and not isinstance(snapshot[json_field], json_type):
                        _block(report, "invalid_history_snapshot_" + kind, 1, fatal=True)
            parent = row.get("parent_id")
            if parent is not None and (not isinstance(parent, str) or not parent.strip()):
                _block(report, "invalid_history_parent_" + kind, 1, fatal=True)
        if rows:
            _block(report, "active_" + kind + "_history_not_preserved", len(rows), detail="history is retained only in the exact source archive")
        for resource in objects[kind].values():
            version = resource.get("version_id")
            if version in (None, ""):
                continue
            if not isinstance(version, str):
                continue
            history = history_rows.get(kind, {}).get(version)
            if history is None or history.get(owner_column) != resource.get("id"):
                _block(report, "invalid_current_version_link_" + kind, 1, fatal=True)
            elif any(row.get("parent_id") == version for row in history_rows.get(kind, {}).values()):
                _block(report, "ambiguous_current_version_link_" + kind, 1, fatal=True)
            else:
                _block(report, "active_" + kind + "_version_link_not_preserved", 1, detail="the old target has no compatible version/history API")
        for row in rows:
            parent = row.get("parent_id")
            if parent is not None:
                parent_row = history_rows.get(kind, {}).get(parent)
                if parent_row is None or parent_row.get(owner_column) != row.get(owner_column):
                    _block(report, "invalid_history_parent_" + kind, 1, fatal=True)
        for row in rows:
            visited = set()
            cursor = row.get("id")
            while cursor is not None:
                if cursor in visited:
                    _block(report, "cyclic_history_" + kind, 1, fatal=True)
                    break
                visited.add(cursor)
                current = history_rows.get(kind, {}).get(cursor)
                cursor = current.get("parent_id") if current else None

    group_ids = group_ids or set()
    if "access_grant" in tables:
        cols = set(_columns(connection, "access_grant"))
        required = SOURCE_REQUIRED["access_grant"]
        if not required.issubset(cols) or cols - required:
            _block(report, "source_schema_mismatch_access_grant", 1, fatal=True)
        rows = [dict(row) for row in connection.execute('SELECT * FROM "access_grant" ORDER BY id')]
        grant_ids = [row.get("id") for row in rows]
        if any(not isinstance(value, str) or not value.strip() for value in grant_ids) or len(grant_ids) != len(set(grant_ids)):
            _block(report, "invalid_or_duplicate_grant_ids", 1, fatal=True)
        seen = set()
        grants: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            kind, resource_id = row.get("resource_type"), row.get("resource_id")
            key = (kind, resource_id)
            if kind not in objects or resource_id not in objects.get(kind, {}):
                _block(report, "dangling_or_unknown_access_grant", 1, fatal=True)
                continue
            uniqueness = (kind, resource_id, row.get("principal_type"), row.get("principal_id"), row.get("permission"))
            if uniqueness in seen:
                _block(report, "duplicate_access_grant", 1, fatal=True)
            seen.add(uniqueness)
            principal_type, permission = row.get("principal_type"), row.get("permission")
            if principal_type not in {"user", "group"} or permission != "read":
                _block(report, "unsupported_access_grant_semantics", 1, fatal=True)
            principal_id = row.get("principal_id")
            if not isinstance(principal_id, str) or not principal_id.strip():
                _block(report, "invalid_access_grant_principal", 1, fatal=True)
            elif principal_type == "user" and principal_id != "*" and principal_id not in user_ids:
                _block(report, "unknown_access_grant_user", 1, fatal=True)
            elif principal_type == "group" and principal_id == "*":
                _block(report, "unsupported_access_grant_semantics", 1, fatal=True)
            elif principal_type == "group" and principal_id not in group_ids:
                _block(report, "unknown_or_unsupported_access_grant_group", 1, fatal=True)
            if not isinstance(row.get("created_at"), int) or isinstance(row.get("created_at"), bool):
                _block(report, "invalid_access_grant_timestamp", 1, fatal=True)
            grants.setdefault(key, []).append(row)
        for key, rows_for_resource in grants.items():
            principal_ids = [row.get("principal_id") for row in rows_for_resource]
            user_principals = [row.get("principal_id") for row in rows_for_resource if row.get("principal_type") == "user"]
            group_principals = [row.get("principal_id") for row in rows_for_resource if row.get("principal_type") == "group"]
            if "*" in user_principals and len(rows_for_resource) != 1:
                _block(report, "public_and_targeted_grants_conflict", 1, fatal=True)
            elif user_principals == ["*"]:
                acl[key] = None
            else:
                read_acl = {}
                if user_principals:
                    read_acl["user_ids"] = user_principals
                if group_principals:
                    read_acl["group_ids"] = group_principals
                acl[key] = {"read": read_acl}
        if rows:
            _block(report, "access_grant_identity_and_time_not_preserved", len(rows), detail="old access_control JSON preserves read behavior, not grant IDs or timestamps")
    for kind in objects:
        for identity in objects[kind]:
            acl.setdefault((kind, identity), {})
    return acl


def _validate_template(
    template: Path,
    *,
    require_named_tool_config: bool = False,
    prepare_user_profile_fields: bool = False,
) -> dict:
    connection = _connect_ro(template)
    try:
        tables = _tables(connection)
        unsafe_objects = connection.execute("SELECT type,COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view') GROUP BY type").fetchall()
        virtual_tables = [table for table in tables if (connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() or [None])[0] and "CREATE VIRTUAL TABLE" in (connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0] or "").upper()]
        if unsafe_objects or virtual_tables:
            raise MigrationError("unsafe_old_template_objects", "old target template contains triggers, views, or virtual tables")
        for table, required in TARGET_REQUIRED.items():
            if table not in tables or not required.issubset(_columns(connection, table)):
                raise MigrationError("old_template_schema_mismatch", f"old target template is missing required table/columns: {table}")
        if prepare_user_profile_fields and IMPORTED_USER_PROFILE_FIELDS & set(_columns(connection, "user")):
            raise MigrationError(
                "old_template_profile_schema_unexpected",
                "profile-enabled conversion requires an unprepared old template without profile columns",
            )
        if require_named_tool_config and not _named_tool_target_config_schema(connection):
            raise MigrationError("old_template_named_config_schema_mismatch", "old target configuration schema is not the expected empty form")
        counts = {table: _count(connection, table) for table in tables}
        dirty = {table: count for table, count in counts.items() if count and table not in MARKER_TABLES}
        if dirty:
            raise MigrationError("old_template_not_empty", "old target template contains pre-existing application data")
        marker_rows = {}
        for table in MARKER_TABLES & set(tables):
            cols = list(_columns(connection, table))
            marker_rows[table] = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
        return {"tables": tables, "counts": counts, "fingerprint": _schema_fingerprint(connection), "markers": marker_rows}
    finally:
        connection.close()


def _validate_function_history(connection: sqlite3.Connection, tables: set[str]) -> bool:
    """Validate the measured function-history subset before target creation."""
    expected = {"id", "function_id", "parent_id", "snapshot", "user_id", "commit_message", "created_at"}
    if "function_history" not in tables or set(_columns(connection, "function_history")) != expected:
        raise ValueError("function history schema is outside the supported shape")
    function_columns = set(_columns(connection, "function")) if "function" in tables else set()
    if not {"id", "user_id", "version_id", "meta"}.issubset(function_columns):
        raise ValueError("function history requires known active function links")
    if "user" not in tables or "id" not in _columns(connection, "user"):
        raise ValueError("function history requires known authors")
    if _count(connection, "function_history") > 10_000:
        raise ValueError("function history exceeds the revision limit")

    function_rows = {
        row["id"]: row
        for row in connection.execute('SELECT id,user_id,version_id,meta FROM "function"')
        if isinstance(row["id"], str) and row["id"]
    }
    user_ids = {row[0] for row in connection.execute('SELECT id FROM "user"')}
    rows = connection.execute(
        'SELECT id,function_id,parent_id,snapshot,user_id,commit_message,created_at FROM "function_history"'
    ).fetchall()
    by_id = {}
    total_snapshot_bytes = 0
    for row in rows:
        revision_id, function_id = row["id"], row["function_id"]
        if not isinstance(revision_id, str) or not revision_id or revision_id in by_id:
            raise ValueError("function history contains an invalid or duplicate revision id")
        if not isinstance(function_id, str) or function_id not in function_rows:
            raise ValueError("function history references an unknown function")
        if not isinstance(row["user_id"], str) or row["user_id"] not in user_ids:
            raise ValueError("function history references an unknown author")
        if row["parent_id"] is not None and (not isinstance(row["parent_id"], str) or not row["parent_id"]):
            raise ValueError("function history has an invalid parent id")
        if row["commit_message"] is not None and not isinstance(row["commit_message"], str):
            raise ValueError("function history has an invalid commit message")
        if not isinstance(row["created_at"], int) or isinstance(row["created_at"], bool):
            raise ValueError("function history has an invalid creation time")
        snapshot_text = row["snapshot"]
        if not isinstance(snapshot_text, str):
            raise ValueError("function history snapshot is not text")
        try:
            encoded = snapshot_text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("function history snapshot contains invalid Unicode") from None
        total_snapshot_bytes += len(encoded)
        if len(encoded) > 256 * 1024 or total_snapshot_bytes > 8 * 1024 * 1024:
            raise ValueError("function history exceeds the snapshot or dataset limit")
        try:
            snapshot = json.loads(
                snapshot_text,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_nonfinite_json,
            )
        except (json.JSONDecodeError, RecursionError, ValueError):
            raise ValueError("function history snapshot is invalid JSON") from None
        try:
            _validate_function_json_value(snapshot)
        except RecursionError:
            raise ValueError("function history snapshot nesting is unsupported") from None
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != {"name", "content", "meta"}
            or not isinstance(snapshot["name"], str)
            or not isinstance(snapshot["content"], str)
            or not isinstance(snapshot["meta"], dict)
        ):
            raise ValueError("function history snapshot is outside the supported object shape")
        by_id[revision_id] = {
            "function_id": function_id,
            "parent_id": row["parent_id"],
        }

    children = {}
    for revision_id, row in by_id.items():
        parent_id = row["parent_id"]
        if parent_id is None:
            continue
        parent = by_id.get(parent_id)
        if parent is None or parent["function_id"] != row["function_id"]:
            raise ValueError("function history parent is missing or belongs to another function")
        children[revision_id] = parent_id
    settled = set()
    for revision_id in by_id:
        path = set()
        current = revision_id
        while current in children and current not in settled:
            if current in path:
                raise ValueError("function history contains a parent cycle")
            path.add(current)
            current = children[current]
        settled.update(path)
    resource_ids = {}
    for revision_id, row in by_id.items():
        resource_ids.setdefault(row["function_id"], set()).add(revision_id)
    for function_id, function in function_rows.items():
        version_id = function["version_id"]
        history_ids = resource_ids.get(function_id, set())
        if history_ids:
            if not isinstance(function["user_id"], str) or function["user_id"] not in user_ids:
                raise ValueError("function history resource has no known owner")
            if not isinstance(version_id, str) or version_id not in history_ids:
                raise ValueError("function current-version link does not match retained history")
            try:
                meta = json.loads(function["meta"], object_pairs_hook=_reject_duplicate_json_keys)
            except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
                raise ValueError("function history active metadata is invalid") from None
            try:
                _validate_function_json_value(meta)
            except RecursionError:
                raise ValueError("function history active metadata nesting is unsupported") from None
            if not isinstance(meta, dict) or not isinstance(meta.get("toggle"), bool):
                raise ValueError("function history active metadata lacks the measured toggle")
        elif isinstance(version_id, str) and version_id:
            raise ValueError("function current-version link has no retained history")
    return True


def _reject_nonfinite_json(value: str):
    raise ValueError("non-finite JSON number")


def _validate_function_json_value(value) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ValueError("function history snapshot contains invalid Unicode") from None
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("function history snapshot contains a non-finite number")
    elif isinstance(value, list):
        for item in value:
            _validate_function_json_value(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_function_json_value(key)
            _validate_function_json_value(item)


def _inspect_source(
    source_db: str | Path,
    uploads_root: str | Path,
    old_template: str | Path,
    *,
    include_private_chat_metadata: bool = False,
    include_named_tool_configuration: bool = False,
    include_user_profile_fields: bool = False,
    include_api_keys: bool = False,
    include_api_key_configuration: bool = False,
    include_user_permissions: bool = False,
    include_function_history: bool = False,
) -> dict:
    started = time.perf_counter()
    source = _db_path(source_db, label="source database")
    template = _db_path(old_template, label="old target template")
    upload_root = Path(uploads_root)
    if upload_root.is_symlink() or not upload_root.is_dir():
        raise MigrationError("uploads_root_missing_or_unsafe", "upload root must be an existing non-symlink directory")
    source_hash = _digest(source)
    template_hash = _digest(template)
    report = {
        "schema_version": 1,
        "status": "eligible_for_review",
        "cutover_eligible": True,
        "fatal": False,
        "blockers": [],
        "source": {"sha256": source_hash, "bytes": source.stat().st_size},
        "target_template": {"sha256": template_hash, "bytes": template.stat().st_size},
        "table_counts": {},
        "schema_fingerprint": None,
        "uploads": [],
        "preserved_domains": ["auth credentials", "users", "branched chats", "function source", "uploaded file bytes", "active tool/model rows", "supported private/user-read/public-read ACL behavior", "complete source SQLite snapshot"],
        "limitations": ["Prepared targets require independent application/API/security review; this tool never switches or activates them."],
    }
    try:
        template_info = _validate_template(
            template,
            require_named_tool_config=(
                include_named_tool_configuration or include_api_key_configuration or include_user_permissions
            ),
            prepare_user_profile_fields=include_user_profile_fields,
        )
        report["target_template"]["schema_fingerprint"] = template_info["fingerprint"]
    except MigrationError as error:
        _block(report, error.code, 1, fatal=True, detail=str(error))
    object_access = {}
    named_tool_config_data = None
    connection = _connect_ro(source)
    try:
        tables = _tables(connection)
        report["schema_fingerprint"] = _schema_fingerprint(connection)
        report["source"]["alembic_version"] = connection.execute('SELECT version_num FROM "alembic_version" LIMIT 1').fetchone()[0] if "alembic_version" in tables and "version_num" in _columns(connection, "alembic_version") and _count(connection, "alembic_version") else None
        for table in tables:
            report["table_counts"][table] = _count(connection, table)
        unknown_tables = sorted(set(tables) - KNOWN_TABLES)
        if unknown_tables:
            _block(report, "unknown_source_tables", len(unknown_tables), fatal=True)
        for table, required in SOURCE_REQUIRED.items():
            if table not in tables or not required.issubset(_columns(connection, table)):
                _block(report, "source_schema_mismatch_" + table, 1, fatal=True)
        object_counts = connection.execute("SELECT type,COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view') GROUP BY type").fetchall()
        if object_counts:
            _block(report, "source_views_or_triggers_unsupported", sum(row[1] for row in object_counts), fatal=True)
        for table in tables:
            if table not in KNOWN_TABLES:
                continue
            count = _count(connection, table)
            if (
                count
                and table not in APP_TABLES | MARKER_TABLES | {"config", "function_history", "access_grant", "group", "group_member", "tool_history", "model_history"}
                and not (table == "api_key" and include_api_keys)
            ):
                _block(report, "unsupported_nonempty_domain_" + table, count, fatal=True)
        for table in ("config", "function_history"):
            if table in tables and _count(connection, table) and not (
                table == "config"
                and (include_named_tool_configuration or include_api_key_configuration or include_user_permissions)
            ) and not (table == "function_history" and include_function_history):
                _block(report, "unsupported_nonempty_domain_" + table, _count(connection, table), detail="full bytes are retained in the source archive; old target behavior is not implemented")
        valid_required = {name: name in tables and required.issubset(_columns(connection, name)) for name, required in SOURCE_REQUIRED.items()}
        profile_fields_verified = False
        if valid_required.get("auth"):
            columns = _columns(connection, "auth")
            allowed = {"id", "email", "password", "active", "mfa", "session_stamp"}
            extras = set(columns) - allowed
            if extras:
                _block(report, "unknown_auth_columns", len(extras), fatal=True)
            for row in connection.execute('SELECT * FROM "auth"'):
                if row["active"] not in (0, 1):
                    _block(report, "invalid_auth_active_flag", 1, fatal=True)
                if "mfa" in columns and not _is_empty(row["mfa"]):
                    _block(report, "mfa_state_unsupported", 1, fatal=True)
                if "session_stamp" in columns and not _is_empty(row["session_stamp"]):
                    _block(report, "session_stamp_not_preserved", 1, detail="old target has no equivalent field; all retained sessions require independent review")
        if valid_required.get("user"):
            columns = _columns(connection, "user")
            extras = set(columns) - (SOURCE_REQUIRED["user"] | {"settings", "info"} | USER_UNSUPPORTED)
            if extras:
                _block(report, "unknown_user_columns", len(extras), fatal=True)
            if include_user_profile_fields:
                if not IMPORTED_USER_PROFILE_FIELDS.issubset(columns):
                    _block(report, "user_profile_source_schema_mismatch", 1, fatal=True)
                else:
                    malformed_schema = any(
                        str(columns[field][2]).upper() != expected_type
                        or columns[field][3] != 0
                        for field, expected_type in IMPORTED_USER_PROFILE_TYPES.items()
                    )
                    if malformed_schema:
                        _block(report, "user_profile_source_schema_mismatch", 1, fatal=True)
                    else:
                        profile_fields_verified = True
                        for row in connection.execute('SELECT bio,gender,date_of_birth FROM "user"'):
                            if not _valid_nullable_profile_string(row["bio"], MAX_IMPORTED_USER_BIO_BYTES):
                                _block(report, "invalid_user_profile_bio", 1, fatal=True)
                                profile_fields_verified = False
                            if not _valid_nullable_profile_string(row["gender"], MAX_IMPORTED_USER_GENDER_BYTES):
                                _block(report, "invalid_user_profile_gender", 1, fatal=True)
                                profile_fields_verified = False
                            if not _valid_nullable_profile_date(row["date_of_birth"]):
                                _block(report, "invalid_user_profile_date_of_birth", 1, fatal=True)
                                profile_fields_verified = False
            for row in connection.execute('SELECT * FROM "user"'):
                for field in USER_UNSUPPORTED & set(columns):
                    if (
                        include_user_profile_fields
                        and profile_fields_verified
                        and field in IMPORTED_USER_PROFILE_FIELDS
                    ):
                        continue
                    if not _is_empty(row[field]):
                        fatal = field in {"oauth", "scim"}
                        _block(report, "unsupported_user_field_" + field, 1, fatal=fatal)
        elif include_user_profile_fields:
            _block(report, "user_profile_source_schema_mismatch", 1, fatal=True)
        if include_user_profile_fields and profile_fields_verified:
            _block(
                report,
                "user_profile_fields_prototype",
                1,
                detail="three nullable profile fields are prepared in a fresh target; other blockers keep cutover disabled",
            )
            report["user_profile_fields"] = {
                "opt_in": True,
                "prepared": True,
                "selected_user_count": _count(connection, "user"),
                "fields": ["bio", "gender", "date_of_birth"],
            }
        elif include_user_profile_fields:
            report["user_profile_fields"] = {
                "opt_in": True,
                "prepared": False,
                "selected_user_count": 0,
                "fields": ["bio", "gender", "date_of_birth"],
            }
        if valid_required.get("chat"):
            columns = _columns(connection, "chat")
            extras = set(columns) - TARGET_REQUIRED["chat"] - CHAT_UNSUPPORTED
            if extras:
                _block(report, "unknown_chat_columns", len(extras), fatal=True)
        if valid_required.get("chat_message"):
            columns = _columns(connection, "chat_message")
            supported = {"id", "chat_id", "user_id", "role", "parent_id", "content"} | MESSAGE_UNSUPPORTED
            if set(columns) - supported:
                _block(report, "unknown_chat_message_columns", len(set(columns) - supported), fatal=True)
        if valid_required.get("file") and set(_columns(connection, "file")) - SOURCE_REQUIRED["file"]:
            _block(report, "unknown_file_columns", len(set(_columns(connection, "file")) - SOURCE_REQUIRED["file"]), fatal=True)
        if valid_required.get("function"):
            columns = _columns(connection, "function")
            extras = set(columns) - SOURCE_REQUIRED["function"] - {"version_id"}
            if extras:
                _block(report, "unknown_function_columns", len(extras), fatal=True)
            if "version_id" in columns and not include_function_history:
                linked = connection.execute("SELECT COUNT(*) FROM \"function\" WHERE version_id IS NOT NULL AND version_id != ''").fetchone()[0]
                if linked:
                    _block(report, "function_revision_link_not_preserved", linked, detail="function_history is retained in the source archive but not usable in the old target")
            known_users = {row[0] for row in connection.execute('SELECT id FROM "user"')} if valid_required.get("user") else set()
            for row in connection.execute('SELECT user_id,is_global FROM "function"'):
                if row["user_id"] is not None and row["user_id"] not in known_users:
                    _block(report, "function_owner_missing", 1, fatal=True)
                if row["is_global"] not in (0, 1):
                    _block(report, "invalid_function_global_flag", 1, fatal=True)
        function_history_selected = False
        if include_function_history:
            try:
                function_history_selected = _validate_function_history(connection, tables)
            except ValueError as error:
                _block(report, "invalid_function_history", 1, fatal=True, detail=str(error))
            if function_history_selected:
                history_count = _count(connection, "function_history")
                _block(
                    report,
                    "function_history_prototype",
                    1,
                    detail="validated function revisions are copied to an opt-in inert sidecar; restoration and cutover remain unsupported",
                )
                report["function_history"] = {
                    "opt_in": True,
                    "prepared": True,
                    "revision_count": history_count,
                    "resource_count": connection.execute(
                        'SELECT COUNT(DISTINCT function_id) FROM "function_history"'
                    ).fetchone()[0],
                }
            else:
                report["function_history"] = {"opt_in": True, "prepared": False, "revision_count": 0, "resource_count": 0}
        _validate_chat(connection, report, include_private_chat_metadata=include_private_chat_metadata)
        private_chat_overrides = (
            _private_chat_metadata_overrides(connection, report)
            if (include_private_chat_metadata and not report["fatal"] and valid_required.get("chat")
                    and valid_required.get("chat_message") and valid_required.get("user"))
            else {}
        )
        file_rows = _source_file_rows(connection, upload_root, report) if valid_required.get("file") else []
        report["uploads"] = [{key: row[key] for key in ("id", "filename", "bytes", "sha256")} for row in file_rows]
        if source.stat().st_size + report.get("upload_bytes", 0) > MAX_INPUT_BYTES:
            _block(report, "dataset_too_large", source.stat().st_size + report.get("upload_bytes", 0), fatal=True)
        auth_ids = {row[0] for row in connection.execute('SELECT id FROM "auth"')} if valid_required.get("auth") else set()
        user_ids = {row[0] for row in connection.execute('SELECT id FROM "user"')} if valid_required.get("user") else set()
        if valid_required.get("auth") and valid_required.get("user") and auth_ids != user_ids:
            _block(report, "auth_user_identity_mismatch", len(auth_ids ^ user_ids) or 1, fatal=True)
        for table in COPYABLE_TABLES & set(tables):
            if "id" in _columns(connection, table):
                null_ids = connection.execute(f'SELECT COUNT(*) FROM "{table}" WHERE id IS NULL OR id = ""').fetchone()[0]
                if null_ids:
                    _block(report, "missing_ids_" + table, null_ids, fatal=True)
        if valid_required.get("user"):
            for row in connection.execute('SELECT role FROM "user"'):
                if row[0] not in {"user", "admin", "pending"}:
                    _block(report, "invalid_user_role", 1, fatal=True)
        if valid_required.get("auth") and valid_required.get("user"):
            auth_emails = {row[0]: row[1] for row in connection.execute('SELECT id,email FROM "auth"')}
            user_emails = {row[0]: row[1] for row in connection.execute('SELECT id,email FROM "user"')}
            if any(auth_emails[identity] != user_emails[identity] for identity in auth_ids & user_ids):
                _block(report, "auth_user_email_mismatch", 1, fatal=True)
        api_key_overrides = (
            _validate_api_key_import(connection, report, set(tables), valid_required)
            if include_api_keys
            else {}
        )
        if valid_required.get("chat") and valid_required.get("user"):
            known_users = {row[0] for row in connection.execute('SELECT id FROM "user"')}
            if any(row[0] not in known_users for row in connection.execute('SELECT user_id FROM "chat"')):
                _block(report, "chat_owner_missing", 1, fatal=True)
        if include_named_tool_configuration or include_api_key_configuration or include_user_permissions:
            config_rows = report["table_counts"].get("config", 0)
            verified = False
            defaults_source_hash = None
            defaults_hash = None
            if not report["fatal"] and valid_required.get("user"):
                try:
                    selected_connection = (
                        _parse_named_tool_connection(connection)
                        if include_named_tool_configuration
                        else None
                    )
                    api_key_settings = (
                        _parse_api_key_configuration(connection)
                        if include_api_key_configuration
                        else None
                    )
                    user_permissions = (
                        _parse_user_permissions(connection)
                        if include_user_permissions
                        else None
                    )
                    defaults, defaults_source_hash, defaults_hash = _load_pinned_old_defaults()
                    target_config_data = json.loads(
                        json.dumps(defaults, sort_keys=True, separators=(",", ":"))
                    )
                    if selected_connection is not None:
                        target_config_data.setdefault("tool_server", {})[
                            "connections"
                        ] = selected_connection
                    if api_key_settings is not None:
                        auth_config = target_config_data.setdefault("auth", {})
                        if not isinstance(auth_config, dict):
                            raise MigrationError(
                                "api_key_configuration_old_defaults_invalid",
                                "pinned old configuration defaults have an unsupported auth shape",
                            )
                        api_key_config = auth_config.setdefault("api_key", {})
                        if not isinstance(api_key_config, dict):
                            raise MigrationError(
                                "api_key_configuration_old_defaults_invalid",
                                "pinned old configuration defaults have an unsupported API-key shape",
                            )
                        api_key_config.update(api_key_settings)
                    if user_permissions is not None:
                        user_config = target_config_data.setdefault("user", {})
                        if not isinstance(user_config, dict):
                            raise MigrationError(
                                "user_permissions_old_defaults_invalid",
                                "pinned old configuration defaults have an unsupported user shape",
                            )
                        user_config["permissions"] = user_permissions
                    named_tool_config_data = {
                        "data": target_config_data,
                        "version": target_config_data["version"],
                    }
                    verified = True
                except MigrationError as error:
                    _block(report, error.code, 1, fatal=True)
            if verified:
                selected_config_rows = (
                    (1 if include_named_tool_configuration else 0)
                    + (3 if include_api_key_configuration else 0)
                    + (1 if include_user_permissions else 0)
                )
                remaining_config_rows = max(config_rows - selected_config_rows, 0)
                if remaining_config_rows:
                    _block(
                        report,
                        "unsupported_nonempty_domain_config",
                        remaining_config_rows,
                        detail="remaining configuration values are retained only in the source archive",
                    )
                if include_named_tool_configuration:
                    _block(
                        report,
                        "named_tool_configuration_prototype",
                        1,
                        detail="selected named-tool connections are mapped; remaining configuration is archived and cutover is disabled",
                    )
                if include_api_key_configuration:
                    _block(
                        report,
                        "api_key_configuration_prototype",
                        1,
                        detail="three selected API-key settings are mapped; remaining configuration is archived and cutover is disabled",
                    )
                if include_user_permissions:
                    _block(
                        report,
                        "user_permissions_prototype",
                        1,
                        detail="the selected boolean permission map is copied; only the API-key feature check is scoped, other permission semantics remain unqualified and cutover is disabled",
                    )
            else:
                remaining_config_rows = config_rows
                if config_rows:
                    _block(
                        report,
                        "unsupported_nonempty_domain_config",
                        config_rows,
                        detail="configuration is retained only in the source archive",
                    )
            if include_named_tool_configuration:
                report["named_tool_configuration"] = {
                    "opt_in": True,
                    "selected_connection_count": len(selected_connection) if verified else 0,
                    "old_defaults_verified": bool(verified),
                    "defaults_source_sha256": defaults_source_hash,
                    "defaults_literal_sha256": defaults_hash,
                    "remaining_config_rows": remaining_config_rows,
                }
            if include_api_key_configuration:
                report["api_key_configuration"] = {
                    "opt_in": True,
                    "selected_key_count": 3 if verified else 0,
                    "old_defaults_verified": bool(verified),
                    "defaults_source_sha256": defaults_source_hash,
                    "defaults_literal_sha256": defaults_hash,
                    "remaining_config_rows": remaining_config_rows,
                }
            if include_user_permissions:
                report["user_permissions"] = {
                    "opt_in": True,
                    "selected_key_count": 1 if verified else 0,
                    "api_keys_permission_present": bool(
                        verified and user_permissions is not None and "api_keys" in user_permissions["features"]
                    ),
                    "remaining_config_rows": remaining_config_rows,
                    "semantics": "only explicit features.api_keys enforcement is in scope; other permission values are copied but unqualified",
                }
        group_memberships = _validate_group_domains(connection, report, user_ids, template)
        object_access = _validate_object_domains(connection, report, user_ids, set(group_memberships))
        for table in COPYABLE_TABLES & set(tables):
            if "id" not in _columns(connection, table):
                continue
            key = "id"
            duplicates = connection.execute(f'SELECT COUNT(*) FROM (SELECT "{key}" FROM "{table}" GROUP BY "{key}" HAVING COUNT(*)>1)').fetchone()[0]
            if duplicates:
                _block(report, "duplicate_ids_" + table, duplicates, fatal=True)
    finally:
        connection.close()
    report["cutover_eligible"] = not report["blockers"]
    report["status"] = "eligible_for_review" if report["cutover_eligible"] else ("refused_fatal_input" if report["fatal"] else "blocked_partial_candidate")
    report["metrics"] = _metrics(started, source.stat().st_size + report.get("upload_bytes", 0))
    return report, file_rows, object_access, group_memberships, private_chat_overrides, named_tool_config_data, api_key_overrides


def inspect_source(
    source_db: str | Path,
    uploads_root: str | Path,
    old_template: str | Path,
    *,
    include_private_chat_metadata: bool = False,
    include_named_tool_configuration: bool = False,
    include_user_profile_fields: bool = False,
    include_api_keys: bool = False,
    include_api_key_configuration: bool = False,
    include_user_permissions: bool = False,
    include_function_history: bool = False,
) -> tuple[dict, list, dict, dict, dict]:
    """Inspect source inputs while preserving the original five-value API."""
    return _inspect_source(
        source_db,
        uploads_root,
        old_template,
        include_private_chat_metadata=include_private_chat_metadata,
        include_named_tool_configuration=include_named_tool_configuration,
        include_user_profile_fields=include_user_profile_fields,
        include_api_keys=include_api_keys,
        include_api_key_configuration=include_api_key_configuration,
        include_user_permissions=include_user_permissions,
        include_function_history=include_function_history,
    )[:5]


def preview(
    source_db: str | Path,
    uploads_root: str | Path,
    old_template: str | Path,
    *,
    include_private_chat_metadata: bool = False,
    include_named_tool_configuration: bool = False,
    include_user_profile_fields: bool = False,
    include_api_keys: bool = False,
    include_api_key_configuration: bool = False,
    include_user_permissions: bool = False,
    include_function_history: bool = False,
) -> dict:
    report, _, _, _, _ = inspect_source(
        source_db,
        uploads_root,
        old_template,
        include_private_chat_metadata=include_private_chat_metadata,
        include_named_tool_configuration=include_named_tool_configuration,
        include_user_profile_fields=include_user_profile_fields,
        include_api_keys=include_api_keys,
        include_api_key_configuration=include_api_key_configuration,
        include_user_permissions=include_user_permissions,
        include_function_history=include_function_history,
    )
    return report


def _insert_rows(source: sqlite3.Connection, target: sqlite3.Connection, table: str, mapping: dict[str, str | None], overrides: dict[str, dict] | None = None) -> int:
    source_rows = [dict(row) for row in source.execute(f'SELECT * FROM "{table}" ORDER BY id')]
    target_columns = _columns(target, table)
    for row in source_rows:
        values = {}
        for dest, src in mapping.items():
            if dest in target_columns:
                values[dest] = row.get(src) if src is not None else None
        for dest, by_id in (overrides or {}).items():
            if dest in target_columns and row.get("id") in by_id:
                values[dest] = by_id[row["id"]]
        columns = list(values)
        placeholders = ",".join("?" for _ in columns)
        quoted = ",".join('"' + col.replace('"', '""') + '"' for col in columns)
        target.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})', [values[column] for column in columns])
    return len(source_rows)


def _copy_domains(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    upload_rows: list[dict],
    runtime_data_root: Path,
    object_access: dict[tuple[str, str], object],
    group_memberships: dict[str, list[str]],
    private_chat_overrides: dict[str, dict[str, str]] | None = None,
    named_tool_config_data: dict | None = None,
    include_user_profile_fields: bool = False,
    api_key_overrides: dict[str, str] | None = None,
) -> dict:
    counts = {}
    counts["auth"] = _insert_rows(source, target, "auth", {"id": "id", "email": "email", "password": "password", "active": "active"})
    user_mapping = {
        "id": "id", "name": "name", "email": "email", "role": "role", "profile_image_url": "profile_image_url",
        "api_key": None, "created_at": "created_at", "updated_at": "updated_at", "last_active_at": "last_active_at",
        "settings": "settings", "info": "info", "oauth_sub": None,
    }
    if include_user_profile_fields:
        user_mapping.update({
            "bio": "bio",
            "gender": "gender",
            "date_of_birth": "date_of_birth",
        })
    counts["user"] = _insert_rows(
        source,
        target,
        "user",
        user_mapping,
        {"api_key": api_key_overrides or {}},
    )
    counts["chat"] = _insert_rows(source, target, "chat", {
        "id": "id", "user_id": "user_id", "title": "title", "share_id": "share_id", "archived": "archived",
        "created_at": "created_at", "updated_at": "updated_at", "chat": "chat", "pinned": "pinned",
        "meta": "meta", "folder_id": "folder_id",
    }, {"chat": {chat_id: value["chat"] for chat_id, value in (private_chat_overrides or {}).items()},
        "meta": {chat_id: value["meta"] for chat_id, value in (private_chat_overrides or {}).items()}})
    file_paths = {row["id"]: str(runtime_data_root / "uploads" / (row["id"] + "_" + row["filename"])) for row in upload_rows}
    counts["file"] = _insert_rows(source, target, "file", {
        "id": "id", "user_id": "user_id", "filename": "filename", "meta": "meta", "created_at": "created_at",
        "hash": "hash", "data": "data", "updated_at": "updated_at", "path": None, "access_control": None,
    }, {"path": file_paths})
    counts["function"] = _insert_rows(source, target, "function", {
        "id": "id", "user_id": "user_id", "name": "name", "type": "type", "content": "content",
        "meta": "meta", "created_at": "created_at", "updated_at": "updated_at", "valves": "valves",
        "is_active": "is_active", "is_global": "is_global",
    })
    if group_memberships:
        encoded_members = {
            group_id: json.dumps(user_ids, ensure_ascii=False, separators=(",", ":"))
            for group_id, user_ids in group_memberships.items()
        }
        counts["group"] = _insert_rows(source, target, "group", {
            "id": "id", "user_id": "user_id", "name": "name", "description": "description",
            "data": "data", "meta": None, "permissions": "permissions", "user_ids": None,
            "created_at": "created_at", "updated_at": "updated_at",
        }, {"user_ids": encoded_members})
        # Current membership row IDs and timestamps remain in the exact source archive.
        counts["group_member"] = 0
    else:
        counts["group"] = 0
        counts["group_member"] = 0
    if named_tool_config_data is not None:
        columns = _columns(target, "config")
        if set(columns) != {"id", "data", "version", "created_at", "updated_at"}:
            raise MigrationError("old_template_named_config_schema_mismatch", "old target configuration schema changed")
        target.execute(
            'INSERT INTO "config" (id,data,version) VALUES (?,?,?)',
            (
                1,
                json.dumps(named_tool_config_data["data"], sort_keys=True, separators=(",", ":")),
                named_tool_config_data["version"],
            ),
        )
        counts["config"] = 1
    for kind, mapping in {
        "tool": {"id": "id", "user_id": "user_id", "name": "name", "content": "content", "specs": "specs", "meta": "meta", "valves": "valves", "updated_at": "updated_at", "created_at": "created_at", "access_control": None},
        "model": {"id": "id", "user_id": "user_id", "base_model_id": "base_model_id", "name": "name", "params": "params", "meta": "meta", "is_active": "is_active", "updated_at": "updated_at", "created_at": "created_at", "access_control": None},
    }.items():
        acl_by_id = {identity: (json.dumps(value, sort_keys=True, separators=(",", ":")) if value is not None else None)
                     for (resource_kind, identity), value in object_access.items() if resource_kind == kind}
        counts[kind] = _insert_rows(source, target, kind, mapping, {"access_control": acl_by_id})
    return counts


def _dir_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def convert(
    source_db: str | Path,
    uploads_root: str | Path,
    old_template: str | Path,
    new_target_dir: str | Path,
    *,
    runtime_data_root: str | Path = "/app/backend/data",
    include_history_sidecar: bool = False,
    include_private_chat_metadata: bool = False,
    include_named_tool_configuration: bool = False,
    include_user_profile_fields: bool = False,
    include_api_keys: bool = False,
    include_api_key_configuration: bool = False,
    include_user_permissions: bool = False,
    include_function_history: bool = False,
    failpoint: Callable[[str], None] | None = None,
) -> dict:
    started = time.perf_counter()
    report, upload_rows, object_access, group_memberships, private_chat_overrides, named_tool_config_data, api_key_overrides = _inspect_source(
        source_db,
        uploads_root,
        old_template,
        include_private_chat_metadata=include_private_chat_metadata,
        include_named_tool_configuration=include_named_tool_configuration,
        include_user_profile_fields=include_user_profile_fields,
        include_api_keys=include_api_keys,
        include_api_key_configuration=include_api_key_configuration,
        include_user_permissions=include_user_permissions,
        include_function_history=include_function_history,
    )
    source = Path(source_db).resolve(strict=True)
    template = Path(old_template).resolve(strict=True)
    target = Path(new_target_dir)
    runtime_root = Path(runtime_data_root)
    if not runtime_root.is_absolute() or ".." in runtime_root.parts:
        raise MigrationError("invalid_runtime_data_root", "runtime data root must be an absolute path without parent traversal")
    if target.exists() or target.is_symlink():
        raise MigrationError("target_already_exists", "new target directory already exists; output is never overwritten", report)
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if source == target or template == target or source.is_relative_to(target) or template.is_relative_to(target):
        raise MigrationError("target_path_overlap", "source, template and fresh target paths must be separate", report)
    if report["fatal"]:
        report["status"] = "refused_fatal_input"
        report["target_created"] = False
        raise MigrationError("fatal_source_input", "source has fatal blockers; no candidate target was created", report)
    attempt = parent / ("." + target.name + ".attempt-" + uuid.uuid4().hex)
    archive_final = parent / (target.name + ".source-archive-" + uuid.uuid4().hex)
    archive_attempt = parent / ("." + archive_final.name + ".attempt")
    attempt.mkdir(mode=0o700)
    archive_attempt.mkdir(mode=0o700)
    try:
        source_copy = archive_attempt / "source.sqlite"
        shutil.copyfile(source, source_copy)
        if _digest(source_copy) != report["source"]["sha256"]:
            raise MigrationError("source_archive_hash_mismatch", "source archive copy failed hash verification", report)
        (attempt / "uploads").mkdir(mode=0o700)
        (archive_attempt / "uploads").mkdir(mode=0o700)
        for upload in upload_rows:
            name = upload["id"] + "_" + upload["filename"]
            shutil.copyfile(upload["source"], attempt / "uploads" / name)
            shutil.copyfile(upload["source"], archive_attempt / "uploads" / name)
            for copy in (attempt / "uploads" / name, archive_attempt / "uploads" / name):
                if _digest(copy) != upload["sha256"]:
                    raise MigrationError("upload_copy_hash_mismatch", "uploaded file copy failed hash verification", report)
        if failpoint:
            failpoint("after_archive")
        app_db = attempt / "webui.db"
        shutil.copyfile(template, app_db)
        source_connection = _connect_ro(source)
        target_connection = sqlite3.connect(app_db)
        target_connection.row_factory = sqlite3.Row
        target_connection.execute("PRAGMA foreign_keys=ON")
        marker_before = _validate_template(
            template,
            require_named_tool_config=(
                include_named_tool_configuration or include_api_key_configuration or include_user_permissions
            ),
            prepare_user_profile_fields=include_user_profile_fields,
        )["markers"]
        try:
            target_connection.execute("BEGIN IMMEDIATE")
            if include_user_profile_fields:
                target_connection.execute('ALTER TABLE "user" ADD COLUMN bio TEXT')
                target_connection.execute('ALTER TABLE "user" ADD COLUMN gender TEXT')
                target_connection.execute('ALTER TABLE "user" ADD COLUMN date_of_birth DATE')
            counts = _copy_domains(
                source_connection, target_connection, upload_rows, runtime_root,
                object_access, group_memberships, private_chat_overrides,
                named_tool_config_data,
                include_user_profile_fields=include_user_profile_fields,
                api_key_overrides=api_key_overrides,
            )
            if failpoint:
                failpoint("after_rows_before_commit")
            target_connection.commit()
            violations = target_connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MigrationError("target_foreign_key_check_failed", "prepared target failed foreign-key check", report)
            check = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise MigrationError("target_integrity_check_failed", "prepared target failed SQLite integrity check", report)
        except Exception:
            target_connection.rollback()
            raise
        finally:
            target_connection.close()
            source_connection.close()
        target_readback = sqlite3.connect(app_db)
        try:
            markers_after = {table: [tuple(row) for row in target_readback.execute(f'SELECT * FROM "{table}" ORDER BY 1')] for table in marker_before}
            if markers_after != marker_before:
                raise MigrationError("migration_markers_changed", "old target migration markers changed", report)
            readback_counts = {table: _count(target_readback, table) for table in COPYABLE_TABLES}
            if named_tool_config_data is not None:
                readback_counts["config"] = _count(target_readback, "config")
            if include_user_profile_fields:
                source_profile = _connect_ro(source)
                try:
                    source_values = [
                        tuple(row)
                        for row in source_profile.execute(
                            'SELECT id,bio,gender,date_of_birth FROM "user" ORDER BY id'
                        )
                    ]
                finally:
                    source_profile.close()
                target_values = [
                    tuple(row)
                    for row in target_readback.execute(
                        'SELECT id,bio,gender,date_of_birth FROM "user" ORDER BY id'
                    )
                ]
                if target_values != source_values:
                    raise MigrationError(
                        "user_profile_readback_mismatch",
                        "prepared user profile fields failed exact readback verification",
                        report,
                    )
                report["user_profile_fields"]["target_readback_exact"] = True
        finally:
            target_readback.close()
        if include_history_sidecar or include_function_history:
            history = write_history_sidecar(
                source,
                app_db,
                attempt / "imported-history.sqlite",
                include_function_history=include_function_history,
                include_tool_model_history=include_history_sidecar,
            )
            report["imported_history_sidecar"] = {
                **history,
                "opt_in": True,
                "runtime_switch": "OPEN_WEBUI_IMPORTED_HISTORY_READS=1",
                "runtime_path": "DATA_DIR/imported-history.sqlite",
                "history_is_functionally_cut_over": False,
            }
            if include_function_history:
                report["imported_history_sidecar"]["function_history_enabled"] = True
        if _digest(source) != report["source"]["sha256"] or _digest(template) != report["target_template"]["sha256"]:
            raise MigrationError("original_changed", "source or template bytes changed during conversion", report)
        if failpoint:
            failpoint("before_publish_candidate")
        report["status"] = "blocked_partial_candidate" if report["blockers"] else "prepared_for_independent_review"
        report["cutover_eligible"] = not report["blockers"]
        report["target_created"] = True
        report["target_database"] = "webui.db"
        report["runtime_data_root"] = str(runtime_root)
        report["target_database_sha256"] = _digest(app_db)
        report["imported_counts"] = counts
        report["target_readback_counts"] = readback_counts
        report["archive"] = {"path": str(archive_final), "source_sqlite": "source.sqlite", "source_sqlite_sha256": _digest(source_copy), "upload_count": len(upload_rows), "exact_source_copy": True, "inside_app_target": False}
        report["limitations"].extend([
            "No cutover or activation is performed by this tool.",
            "Archived-only config, unselected history domains, session-stamp and other blocker domains remain outside this opt-in history prototype.",
            "Post-import writes must be exported separately before restoring any earlier target; this prototype has no reverse importer.",
        ])
        report["metrics"] = _metrics(started, report["source"]["bytes"] + report.get("upload_bytes", 0))
        report["metrics"]["archive_bytes"] = _dir_size(archive_attempt)
        report["metrics"]["target_bytes"] = _dir_size(attempt)
        output_bytes = report["metrics"]["archive_bytes"] + report["metrics"]["target_bytes"]
        output_limit = 4 * report["metrics"]["input_bytes"] + MAX_INPUT_BYTES
        report["metrics"]["output_bytes"] = output_bytes
        report["metrics"]["candidate_payload_bytes"] = output_bytes
        report["metrics"]["provisional_output_limit_bytes"] = output_limit
        report["metrics"]["within_provisional_output_limit"] = output_bytes <= output_limit
        report["metrics"]["within_provisional_elapsed_limit"] = report["metrics"]["elapsed_seconds"] <= 30
        report["metrics"]["within_provisional_peak_rss_limit"] = report["metrics"]["process_peak_rss_bytes"] <= 256 * 1024 * 1024
        if not (report["metrics"]["within_provisional_output_limit"] and report["metrics"]["within_provisional_elapsed_limit"] and report["metrics"]["within_provisional_peak_rss_limit"]):
            raise MigrationError("resource_budget_exceeded", "conversion exceeded a provisional experiment resource limit", report)
        report_path = attempt / "report.json"
        report["target_dir"] = str(target)
        for _ in range(3):
            report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            measured_target = _dir_size(attempt)
            measured_archive = _dir_size(archive_attempt)
            if report["metrics"].get("target_bytes") == measured_target and report["metrics"].get("archive_bytes") == measured_archive:
                break
            report["metrics"]["target_bytes"] = measured_target
            report["metrics"]["archive_bytes"] = measured_archive
            report["metrics"]["output_bytes"] = measured_target + measured_archive
            report["metrics"]["candidate_payload_bytes"] = measured_target + measured_archive
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if target.exists():
            raise MigrationError("target_already_exists", "new target appeared during conversion; refusing overwrite", report)
        if archive_final.exists():
            raise MigrationError("archive_target_already_exists", "source archive destination appeared during conversion", report)
        os.replace(archive_attempt, archive_final)
        os.replace(attempt, target)
        return report
    except Exception as error:
        failure = {"status": "failed_retained_attempt", "error_code": getattr(error, "code", "conversion_failed"), "source_sha256": report["source"]["sha256"], "template_sha256": report["target_template"]["sha256"]}
        try:
            (attempt / "failure.json").write_text(json.dumps(failure, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            if archive_attempt.exists():
                (archive_attempt / "failure.json").write_text(json.dumps(failure, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        if isinstance(error, MigrationError):
            error.report = {**report, **failure, "failed_attempt_dir": str(attempt)}
            raise
        raise MigrationError("conversion_failed", "conversion failed; incomplete attempt retained for inspection", {**report, **failure, "failed_attempt_dir": str(attempt)}) from error


def _emit(report: dict) -> None:
    print(json.dumps(report, sort_keys=True, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preview or prepare a bounded fresh-target SQLite conversion.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preview", "convert"):
        command = sub.add_parser(name)
        command.add_argument("--source-db", required=True)
        command.add_argument("--uploads-root", required=True)
        command.add_argument("--empty-old-target-template", required=True)
        if name == "convert":
            command.add_argument("--new-target-dir", required=True)
            command.add_argument("--runtime-data-root", default="/app/backend/data", help="data directory used by the old runtime for persisted upload paths")
            command.add_argument("--include-history-sidecar", action="store_true", help="write the bounded opt-in Tool/Model history prototype under the fresh target data directory")
        command.add_argument("--include-private-chat-metadata", action="store_true", help="map validated same-owner private chat authors and read metadata into existing chat JSON/meta")
        command.add_argument(
            "--include-named-tool-configuration",
            action="store_true",
            help="map only the measured no-auth named-tool connection and its existing user/read grant",
        )
        command.add_argument(
            "--include-user-profile-fields",
            action="store_true",
            help="prepare the opt-in nullable bio/gender/date_of_birth fields on a fresh old-target copy",
        )
        command.add_argument(
            "--include-api-keys",
            action="store_true",
            help="copy only validated supported opaque keys to their matching legacy user fields",
        )
        command.add_argument(
            "--include-api-key-configuration",
            action="store_true",
            help="map only the three measured API-key policy settings into the old nested configuration",
        )
        command.add_argument(
            "--include-user-permissions",
            action="store_true",
            help="map the exact measured nested boolean user-permissions setting into old configuration",
        )
        command.add_argument(
            "--include-function-history",
            action="store_true",
            help="copy validated function revisions into the opt-in inert history sidecar",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            report = preview(
                args.source_db,
                args.uploads_root,
                args.empty_old_target_template,
                include_private_chat_metadata=args.include_private_chat_metadata,
                include_named_tool_configuration=args.include_named_tool_configuration,
                include_user_profile_fields=args.include_user_profile_fields,
                include_api_keys=args.include_api_keys,
                include_api_key_configuration=args.include_api_key_configuration,
                include_user_permissions=args.include_user_permissions,
                include_function_history=args.include_function_history,
            )
        else:
            report = convert(
                args.source_db,
                args.uploads_root,
                args.empty_old_target_template,
                args.new_target_dir,
                runtime_data_root=args.runtime_data_root,
                include_history_sidecar=args.include_history_sidecar,
                include_private_chat_metadata=args.include_private_chat_metadata,
                include_named_tool_configuration=args.include_named_tool_configuration,
                include_user_profile_fields=args.include_user_profile_fields,
                include_api_keys=args.include_api_keys,
                include_api_key_configuration=args.include_api_key_configuration,
                include_user_permissions=args.include_user_permissions,
                include_function_history=args.include_function_history,
            )
    except MigrationError as error:
        report = {"status": "refused", "error_code": error.code, **error.report}
        _emit(report)
        return 3 if error.code == "fatal_source_input" else 2
    except sqlite3.Error:
        _emit({"status": "refused", "error_code": "sqlite_database_error", "detail": "SQLite could not read the supplied source or template; originals were not modified"})
        return 2
    _emit(report)
    return 3 if report.get("cutover_eligible") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
