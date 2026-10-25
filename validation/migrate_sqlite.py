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
import hashlib
import json
import os
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Callable


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
CHAT_UNSUPPORTED = {"tasks", "summary", "last_read_at", "current_message_id", "variables", "timer_at"}
MESSAGE_UNSUPPORTED = {
    "output", "model_id", "files", "sources", "embeds", "done", "status_history",
    "error", "usage", "created_at", "updated_at", "context_summary", "meta",
}


class MigrationError(Exception):
    def __init__(self, code: str, message: str, report: dict | None = None):
        super().__init__(message)
        self.code = code
        self.report = report or {}


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
    users = {row[0] for row in connection.execute('SELECT id FROM "user"')}
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


def _validate_chat(connection: sqlite3.Connection, report: dict) -> None:
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
                if field != "current_message_id":
                    _block(report, "unsupported_chat_field_" + field, 1)


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


def _validate_object_domains(connection: sqlite3.Connection, report: dict, user_ids: set[str]) -> dict[tuple[str, str], object]:
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
            if resource is None or row.get("user_id") != resource.get("user_id"):
                _block(report, "history_resource_or_owner_mismatch_" + kind, 1, fatal=True)
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
                        if snapshot[field] is not None and not isinstance(snapshot[field], str):
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
            if row.get("principal_type") != "user" or row.get("permission") != "read":
                _block(report, "unsupported_access_grant_semantics", 1, fatal=True)
            principal_id = row.get("principal_id")
            if not isinstance(principal_id, str) or not principal_id.strip() or (principal_id != "*" and principal_id not in user_ids):
                _block(report, "unknown_access_grant_user", 1, fatal=True)
            if not isinstance(row.get("created_at"), int) or isinstance(row.get("created_at"), bool):
                _block(report, "invalid_access_grant_timestamp", 1, fatal=True)
            grants.setdefault(key, []).append(row)
        for key, rows_for_resource in grants.items():
            principal_ids = [row.get("principal_id") for row in rows_for_resource]
            if "*" in principal_ids and len(rows_for_resource) != 1:
                _block(report, "public_and_targeted_grants_conflict", 1, fatal=True)
            elif principal_ids == ["*"]:
                acl[key] = None
            else:
                acl[key] = {"read": {"user_ids": principal_ids}}
        if rows:
            _block(report, "access_grant_identity_and_time_not_preserved", len(rows), detail="old access_control JSON preserves read behavior, not grant IDs or timestamps")
    for kind in objects:
        for identity in objects[kind]:
            acl.setdefault((kind, identity), {})
    return acl


def _validate_template(template: Path) -> dict:
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


def inspect_source(source_db: str | Path, uploads_root: str | Path, old_template: str | Path) -> dict:
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
        template_info = _validate_template(template)
        report["target_template"]["schema_fingerprint"] = template_info["fingerprint"]
    except MigrationError as error:
        _block(report, error.code, 1, fatal=True, detail=str(error))
    object_access = {}
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
        for table in ("group", "group_member"):
            if table in tables and _count(connection, table):
                _block(report, "security_domain_nonempty_" + table, _count(connection, table), fatal=True)
        for table in tables:
            if table not in KNOWN_TABLES:
                continue
            count = _count(connection, table)
            if count and table not in APP_TABLES | MARKER_TABLES | {"config", "function_history", "access_grant", "tool_history", "model_history"}:
                _block(report, "unsupported_nonempty_domain_" + table, count, fatal=True)
        for table in ("config", "function_history"):
            if table in tables and _count(connection, table):
                _block(report, "unsupported_nonempty_domain_" + table, _count(connection, table), detail="full bytes are retained in the source archive; old target behavior is not implemented")
        valid_required = {name: name in tables and required.issubset(_columns(connection, name)) for name, required in SOURCE_REQUIRED.items()}
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
            for row in connection.execute('SELECT * FROM "user"'):
                for field in USER_UNSUPPORTED & set(columns):
                    if not _is_empty(row[field]):
                        fatal = field in {"oauth", "scim"}
                        _block(report, "unsupported_user_field_" + field, 1, fatal=fatal)
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
            if "version_id" in columns:
                linked = connection.execute('SELECT COUNT(*) FROM "function" WHERE version_id IS NOT NULL AND version_id != ""').fetchone()[0]
                if linked:
                    _block(report, "function_revision_link_not_preserved", linked, detail="function_history is retained in the source archive but not usable in the old target")
            known_users = {row[0] for row in connection.execute('SELECT id FROM "user"')} if valid_required.get("user") else set()
            for row in connection.execute('SELECT user_id,is_global FROM "function"'):
                if row["user_id"] is not None and row["user_id"] not in known_users:
                    _block(report, "function_owner_missing", 1, fatal=True)
                if row["is_global"] not in (0, 1):
                    _block(report, "invalid_function_global_flag", 1, fatal=True)
        _validate_chat(connection, report)
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
        if valid_required.get("chat") and valid_required.get("user"):
            known_users = {row[0] for row in connection.execute('SELECT id FROM "user"')}
            if any(row[0] not in known_users for row in connection.execute('SELECT user_id FROM "chat"')):
                _block(report, "chat_owner_missing", 1, fatal=True)
        object_access = _validate_object_domains(connection, report, user_ids)
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
    return report, file_rows, object_access


def preview(source_db: str | Path, uploads_root: str | Path, old_template: str | Path) -> dict:
    report, _, _ = inspect_source(source_db, uploads_root, old_template)
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


def _copy_domains(source: sqlite3.Connection, target: sqlite3.Connection, upload_rows: list[dict], runtime_data_root: Path, object_access: dict[tuple[str, str], object]) -> dict:
    counts = {}
    counts["auth"] = _insert_rows(source, target, "auth", {"id": "id", "email": "email", "password": "password", "active": "active"})
    counts["user"] = _insert_rows(source, target, "user", {
        "id": "id", "name": "name", "email": "email", "role": "role", "profile_image_url": "profile_image_url",
        "api_key": None, "created_at": "created_at", "updated_at": "updated_at", "last_active_at": "last_active_at",
        "settings": "settings", "info": "info", "oauth_sub": None,
    })
    counts["chat"] = _insert_rows(source, target, "chat", {
        "id": "id", "user_id": "user_id", "title": "title", "share_id": "share_id", "archived": "archived",
        "created_at": "created_at", "updated_at": "updated_at", "chat": "chat", "pinned": "pinned",
        "meta": "meta", "folder_id": "folder_id",
    })
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


def convert(source_db: str | Path, uploads_root: str | Path, old_template: str | Path, new_target_dir: str | Path, *, runtime_data_root: str | Path = "/app/backend/data", failpoint: Callable[[str], None] | None = None) -> dict:
    started = time.perf_counter()
    report, upload_rows, object_access = inspect_source(source_db, uploads_root, old_template)
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
        marker_before = _validate_template(template)["markers"]
        try:
            target_connection.execute("BEGIN IMMEDIATE")
            counts = _copy_domains(source_connection, target_connection, upload_rows, runtime_root, object_access)
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
        finally:
            target_readback.close()
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
            "Archived-only config, function-history, session-stamp and other blocker domains are not functional in the old target.",
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
    args = parser.parse_args(argv)
    try:
        if args.command == "preview":
            report = preview(args.source_db, args.uploads_root, args.empty_old_target_template)
        else:
            report = convert(args.source_db, args.uploads_root, args.empty_old_target_template, args.new_target_dir, runtime_data_root=args.runtime_data_root)
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
