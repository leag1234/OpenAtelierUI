# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Export one eligible private text chat as a separate import artifact."""
import argparse
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import time
from pathlib import Path

MAX_SOURCE = 64 * 1024 * 1024
MAX_OUTPUT = 4 * 1024 * 1024
MAX_NODES = 4096
MAX_DEPTH = 64
DEADLINE = 10.0
MARKER = "_mdk_private_saved_work"
REQUIRED = {"id", "user_id", "title", "created_at", "updated_at", "share_id", "archived", "chat", "pinned", "meta", "folder_id"}
OPTIONAL = {"current_message_id", "variables", "last_read_at"}
REFERENCES = {"file", "files", "file_id", "file_ids", "attachment", "attachments", "source", "sources", "image", "images", "image_url", "audio", "video", "tool_call", "tool_calls", "tool_call_id", "function_call", "function_calls", "artifact", "artifacts", "url_citation", "knowledge_file", "upload_id"}
DENIED = {"access_control", "access_grants", "permissions", "permission", "group", "groups", "group_ids", "public", "is_public", "tools", "tool", "functions", "function", "executable", "execution", "command", "code", "tags"}


class Refusal(Exception):
    def __init__(self, reason): self.reason = reason


def require(condition, reason):
    if not condition: raise Refusal(reason)


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise Refusal("invalid_arguments")


def _check(value, depth=0):
    require(depth <= MAX_DEPTH, "json_depth_exceeded")
    if isinstance(value, str):
        try:
            value.encode("utf-8", "strict")
        except UnicodeError:
            raise Refusal("invalid_unicode")
    elif isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str), "invalid_json_shape")
            _check(key, depth + 1); _check(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check(item, depth + 1)
    elif value is not None and type(value) not in (bool, int, float):
        raise Refusal("invalid_json_shape")
    elif isinstance(value, float):
        require(float("-inf") < value < float("inf"), "invalid_json_number")


def _loads(raw, reason="invalid_chat_json", limit=MAX_OUTPUT):
    if isinstance(raw, bytes):
        require(len(raw) <= limit, "input_limit_exceeded")
        try:
            raw = raw.decode("utf-8", "strict")
        except UnicodeError:
            raise Refusal(reason)
    elif isinstance(raw, str):
        try:
            require(len(raw.encode("utf-8", "strict")) <= limit, "input_limit_exceeded")
        except UnicodeError:
            raise Refusal("invalid_unicode")
    require(isinstance(raw, str), reason)

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, reason)
            result[key] = value
        return result

    def constant(_):
        raise Refusal(reason)

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        _check(value)
        return value
    except Refusal:
        raise
    except (ValueError, TypeError, RecursionError):
        raise Refusal(reason)


def _dumps(value):
    try:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise Refusal("invalid_chat_json")
    require(len(data) <= MAX_OUTPUT, "output_limit_exceeded")
    return data


def _source(path, started=None):
    path = Path(path)
    try:
        require(not path.is_symlink(), "source_not_regular")
        info = path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_SOURCE, "source_not_eligible")
        require(not any(Path(str(path) + suffix).exists() or Path(str(path) + suffix).is_symlink() for suffix in ("-wal", "-shm", "-journal")), "source_sidecar_present")
        digest = hashlib.sha256()
        total = 0
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            require(stat.S_ISREG(opened.st_mode) and (info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino), "source_changed")
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(block); require(total <= MAX_SOURCE, "source_limit_exceeded")
                require(started is None or time.monotonic() - started <= DEADLINE, "deadline_exceeded")
                digest.update(block)
        after = path.stat()
        require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "source_changed")
        return info, digest.hexdigest()
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("source_unavailable")


def _graph(raw, owner, title):
    graph = _loads(raw, limit=MAX_OUTPUT)
    require(isinstance(graph, dict) and isinstance(graph.get("history"), dict), "unsupported_chat_graph")
    history = graph["history"]
    nodes, selected = history.get("messages"), history.get("currentId")
    require(isinstance(nodes, dict) and 1 <= len(nodes) <= MAX_NODES, "invalid_chat_graph")
    require(isinstance(selected, str) and selected in nodes, "invalid_selected_message")
    require(graph.get("title", title) == title, "chat_title_mismatch")
    require(graph.get("tags") in (None, []), "unsupported_chat_tags")
    for node_id, node in nodes.items():
        require(isinstance(node_id, str) and node_id and isinstance(node, dict) and node.get("id") == node_id, "invalid_chat_graph")
        require(isinstance(node.get("role"), str) and node["role"] in {"user", "assistant", "system"} and isinstance(node.get("content"), str), "unsupported_chat_content")
        parent, children = node.get("parentId"), node.get("childrenIds")
        require(parent is None or isinstance(parent, str) and parent in nodes, "invalid_chat_graph")
        require(isinstance(children, list) and len(set(children)) == len(children) and all(isinstance(child, str) and child in nodes for child in children), "invalid_chat_graph")
        require("user_id" not in node or node["user_id"] == owner, "foreign_chat_author")
        if "user" in node:
            profile = node["user"]
            require(node["role"] == "user" and isinstance(profile, dict) and not set(profile) - {"id", "name"}, "unsupported_chat_author")
            require(profile.get("id") == owner and isinstance(profile.get("name"), str), "foreign_chat_author")
    roots = [key for key, node in nodes.items() if node.get("parentId") is None]
    require(len(roots) == 1, "invalid_chat_graph")
    for key, node in nodes.items():
        require(node.get("parentId") is None or key in nodes[node["parentId"]]["childrenIds"], "invalid_chat_graph")
        require(all(nodes[child].get("parentId") == key for child in node["childrenIds"]), "invalid_chat_graph")
    visited, pending = set(), [roots[0]]
    while pending:
        key = pending.pop()
        require(key not in visited, "invalid_chat_graph")
        visited.add(key); pending.extend(nodes[key]["childrenIds"])
    require(len(visited) == len(nodes), "disconnected_chat_graph")
    ancestry, seen, cursor = [], set(), selected
    while cursor is not None:
        require(cursor not in seen, "invalid_chat_graph")
        seen.add(cursor); ancestry.append(cursor); cursor = nodes[cursor].get("parentId")
    require(ancestry[-1] == roots[0], "disconnected_chat_graph")
    chain = list(reversed(ancestry))
    flat = graph.get("messages")
    require(isinstance(flat, list) and len(flat) <= MAX_NODES and [x.get("id") if isinstance(x, dict) else None for x in flat] == chain, "flat_chain_mismatch")
    for item in flat:
        node = nodes[item["id"]]
        require(item.get("role") == node.get("role") and item.get("content") == node.get("content"), "flat_chain_mismatch")
        require("user_id" not in item or item["user_id"] == owner, "foreign_chat_author")
        if "user" in item:
            profile = item["user"]
            require(item.get("role") == "user" and isinstance(profile, dict) and not set(profile) - {"id", "name"} and profile.get("id") == owner and isinstance(profile.get("name"), str), "foreign_chat_author")
    # Detect reference-bearing graph fields wherever they occur, including flat history.
    stack = [graph]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, item in value.items():
                require(key.lower() not in REFERENCES or item in (None, "", [], {}), "unsupported_chat_reference")
                require(key.lower() not in DENIED or item in (None, "", [], {}), "unsupported_chat_rights")
                require(key.lower() != "type" or not isinstance(item, str) or item.lower() not in {"image", "audio", "video", "tool", "function", "file"}, "unsupported_chat_reference")
                if key == "user_id":
                    require(item == owner, "foreign_chat_author")
                if key == "user":
                    require(isinstance(item, dict) and not set(item) - {"id", "name"} and item.get("id") == owner and isinstance(item.get("name"), str), "foreign_chat_author")
                stack.append(item)
        elif isinstance(value, list):
            stack.extend(value)
    return graph, len(nodes), len(chain), selected


def _read(source, owner, chat_id, started, graph_reader=None):
    path = Path(source)
    before, digest = _source(path, started)
    require(time.monotonic() - started <= DEADLINE, "deadline_exceeded")
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON"); db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(time.monotonic() - started > DEADLINE), 1000)
        schema = db.execute("SELECT type,sql FROM sqlite_master WHERE name='chat'").fetchone()
        require(schema is not None and schema["type"] == "table" and "VIRTUAL TABLE" not in (schema["sql"] or "").upper(), "unsupported_chat_schema")
        info = db.execute("PRAGMA table_info('chat')").fetchall()
        columns = {x["name"]: x for x in info}
        require(REQUIRED <= columns.keys() and not (columns.keys() - REQUIRED - OPTIONAL), "unsupported_chat_schema")
        primary = [x["name"] for x in info if x["pk"]]
        identity_index = False
        if not primary:
            for index in db.execute('PRAGMA index_list("chat")'):
                if index["unique"] and not index["partial"]:
                    quoted = '"' + index["name"].replace('"', '""') + '"'
                    indexed = [x["name"] for x in db.execute("PRAGMA index_info(" + quoted + ")")]
                    identity_index |= indexed == ["id"]
        require(primary == ["id"] or not primary and identity_index, "ambiguous_chat_identity")
        types = {"id": {"TEXT", "VARCHAR", "VARCHAR(255)", "CHAR", "CLOB", ""}, "user_id": {"TEXT", "VARCHAR", "VARCHAR(255)", "CHAR", "CLOB", ""}, "title": {"TEXT", "VARCHAR", "CHAR", "CLOB", ""}, "created_at": {"INTEGER", "INT", "BIGINT", "SMALLINT", "DATETIME"}, "updated_at": {"INTEGER", "INT", "BIGINT", "SMALLINT", "DATETIME"}, "share_id": {"TEXT", "VARCHAR", "VARCHAR(255)", "CHAR", "CLOB", ""}, "archived": {"BOOLEAN", "INTEGER", "INT"}, "chat": {"JSON", "TEXT", ""}, "pinned": {"BOOLEAN", "INTEGER", "INT"}, "meta": {"JSON", "TEXT", ""}, "folder_id": {"TEXT", "VARCHAR", "CHAR", "CLOB", ""}}
        require(all((columns[key]["type"] or "").upper() in allowed for key, allowed in types.items()), "unsupported_chat_schema")
        optional_types = {"current_message_id": {"TEXT", "VARCHAR", "CHAR", "CLOB", ""}, "variables": {"JSON", "TEXT", ""}, "last_read_at": {"INTEGER", "INT", "BIGINT", "SMALLINT"}}
        require(all((columns[key]["type"] or "").upper() in optional_types[key] for key in columns.keys() & OPTIONAL), "unsupported_chat_schema")
        names = ["id", "user_id", "title", "created_at", "updated_at", "share_id", "archived", "chat", "pinned", "meta", "folder_id"] + sorted(columns.keys() & OPTIONAL)
        query = "SELECT " + ",".join('"' + name + '"' for name in names) + ' FROM "chat" WHERE "id"=?'
        matches = db.execute(query, (chat_id,)).fetchmany(2)
        require(len(matches) <= 1, "ambiguous_chat_identity")
        row = matches[0] if matches else None
        require(row is not None, "chat_not_found")
        require(isinstance(row["id"], str) and isinstance(row["user_id"], str) and row["user_id"] == owner, "chat_not_owned")
        require(row["share_id"] is None and row["folder_id"] is None, "shared_or_foldered_chat")
        require(type(row["archived"]) is int and row["archived"] == 0, "archived_chat")
        pinned = False if row["pinned"] is None else row["pinned"]
        require(type(pinned) in (bool, int) and pinned in (False, 0, 1), "invalid_pinned_value")
        for key in ("created_at", "updated_at"):
            require(type(row[key]) is int and row[key] >= 0, "invalid_chat_timestamp")
        require(isinstance(row["title"], str) and len(row["title"].encode("utf-8", "strict")) <= 8192, "invalid_chat_title")
        meta = _loads(row["meta"], "invalid_chat_metadata", limit=1024)
        require(isinstance(meta, dict), "invalid_chat_metadata")
        marker_omitted = bool(meta)
        if meta:
            require(set(meta) == {MARKER} and isinstance(meta[MARKER], dict), "unsupported_chat_metadata")
            marker = meta[MARKER]
            require(set(marker) == {"version", "last_read_at"} and type(marker["version"]) is int and marker["version"] == 1, "unsupported_saved_work_marker")
            require(marker["last_read_at"] is None or type(marker["last_read_at"]) is int and marker["last_read_at"] >= 0, "unsupported_saved_work_marker")
        require("variables" not in columns or row["variables"] is None, "unsupported_chat_variables")
        require("last_read_at" not in columns or row["last_read_at"] is None, "unsupported_chat_read_state")
        reader = graph_reader or _graph
        graph, node_count, chain_count, selected = reader(row["chat"], owner, row["title"], db) if graph_reader else reader(row["chat"], owner, row["title"])
        require("current_message_id" not in columns or row["current_message_id"] in (None, "", selected), "selected_message_mismatch")
        require(time.monotonic() - started <= DEADLINE, "deadline_exceeded")
        form = {"chat": graph, "meta": {}, "pinned": bool(pinned), "folder_id": None, "current_message_id": selected, "created_at": row["created_at"], "updated_at": row["updated_at"]}
        artifact = {"chats": [form]}
        data = _dumps(artifact)
        after, digest_after = _source(path, started)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) and digest == digest_after, "source_changed")
        require(time.monotonic() - started <= DEADLINE, "deadline_exceeded")
        receipt = {"source_sha256": digest, "output_sha256": hashlib.sha256(data).hexdigest(), "output_bytes": len(data), "chat_count": 1, "node_count": node_count, "selected_chain_nodes": chain_count, "saved_work_marker_omitted": marker_omitted}
        return data, receipt
    except Refusal:
        raise
    except sqlite3.Error:
        if time.monotonic() - started > DEADLINE:
            raise Refusal("deadline_exceeded")
        raise Refusal("source_unavailable")
    except (OSError, ValueError, TypeError, RecursionError):
        raise Refusal("invalid_source_data")
    finally:
        if db is not None:
            db.close()


def _publish(output_path, source_path, expected_hash, data, started, prepublish=None):
    output, source = Path(output_path), Path(source_path)
    try:
        require(output.resolve(strict=False) != source.resolve(strict=True), "output_is_source")
        require(output.parent.is_dir() and not output.is_symlink() and not output.exists(), "output_not_available")
        require(_source(source, started)[1] == expected_hash, "source_changed")
        require(time.monotonic() - started <= DEADLINE, "deadline_exceeded")
        fd, temp = tempfile.mkstemp(prefix=".private-chat-export-", dir=str(output.parent))
        published, identity = False, None
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            require(time.monotonic() - started <= DEADLINE, "deadline_exceeded")
            require(_source(source, started)[1] == expected_hash, "source_changed")
            if prepublish is not None:
                prepublish()
            info = os.stat(temp, follow_symlinks=False); identity = (info.st_dev, info.st_ino)
            os.link(temp, output)
            published = True
            os.unlink(temp)
            directory = os.open(output.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temp)
            except OSError:
                pass
            if published:
                try:
                    current = os.stat(output, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == identity:
                        os.unlink(output)
                except OSError:
                    pass
            raise
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("output_failed")


def _args(argv):
    parser = Parser(add_help=True)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "export"):
        sub = subs.add_parser(command)
        for name in ("source", "owner-id", "chat-id"):
            sub.add_argument("--" + name, required=True)
        if command == "export":
            sub.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    started = time.monotonic()
    try:
        args = _args(argv)
        require(all(args.__dict__[key] and len(args.__dict__[key]) <= 255 for key in ("owner_id", "chat_id")), "invalid_selection")
        require(not any(ord(char) < 32 or ord(char) == 127 for char in args.owner_id + args.chat_id), "invalid_selection")
        data, receipt = _read(args.source, args.owner_id, args.chat_id, started)
        if args.command == "export":
            _publish(args.output, args.source, receipt["source_sha256"], data, started)
        receipt.update({"artifact_written": args.command == "export", "elapsed_ms": int((time.monotonic() - started) * 1000)})
        result, code = {"ok": True, "command": args.command, "result": receipt}, 0
    except Refusal as error:
        result, code = {"ok": False, "reason": error.reason}, 2
    except SystemExit as error:
        if error.code == 0:
            return 0
        result, code = {"ok": False, "reason": "invalid_arguments"}, 2
    except Exception:
        result, code = {"ok": False, "reason": "export_failed"}, 2
    print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
