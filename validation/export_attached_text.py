# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Export one private chat and its owned text upload as a deterministic lab bundle."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
import zipfile
from io import BytesIO
from pathlib import Path

try:
    from . import export_private_text as core
except ImportError:
    import export_private_text as core

FORMAT = "openatelier-attached-text-bundle-v1"
TOKEN = "attachment-1"
MEMBER = "attachment.txt"
MAX_FILE = 256 * 1024
MAX_BUNDLE = 5 * 1024 * 1024
ITEM_KEYS = {"error", "file", "id", "itemId", "name", "size", "status", "type", "url"}
DTO_KEYS = {"created_at", "data", "filename", "hash", "id", "meta", "updated_at", "user_id"}
FILE_COLUMNS = {"id": "TEXT", "user_id": "TEXT", "filename": "TEXT", "path": "TEXT", "hash": "TEXT", "meta": "JSON", "data": "JSON", "created_at": "INTEGER", "updated_at": "BIGINT", "access_control": "JSON"}


def _need(value, reason):
    core.require(value, reason)


def _refs(value):
    if value in (None, "", [], {}):
        return []
    _need(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict), "unsupported_chat_reference")
    return value


def _reference_shape(ref):
    _need(set(ref) == ITEM_KEYS, "unsupported_chat_reference")
    file_id = ref.get("id")
    _need(isinstance(file_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", file_id), "unsupported_chat_reference")
    _need(ref.get("type") == "file" and ref.get("status") == "uploaded" and ref.get("error") == "", "unsupported_chat_reference")
    _need(ref.get("url") == "/api/v1/files/" + file_id, "unsupported_chat_reference")
    _need(isinstance(ref.get("itemId"), str) and 1 <= len(ref["itemId"]) <= 128 and not any(ord(c) < 32 or ord(c) == 127 for c in ref["itemId"]), "unsupported_chat_reference")
    _need(type(ref.get("size")) is int and 1 <= ref["size"] <= MAX_FILE, "unsupported_chat_reference")
    file = ref.get("file")
    _need(isinstance(file, dict) and set(file) == DTO_KEYS, "unsupported_chat_reference")
    meta = file.get("meta")
    _need(isinstance(meta, dict) and set(meta) in ({"name", "content_type", "size", "data"}, {"name", "content_type", "size", "data", "file_hash"}), "unsupported_chat_reference")
    name = ref.get("name")
    _need(isinstance(name, str) and 1 <= len(name.encode("utf-8", "strict")) <= 255 and name not in {".", ".."}, "unsupported_chat_reference")
    _need(not any(c in name for c in "/\\\x00") and not any(ord(c) < 32 or ord(c) == 127 for c in name), "unsupported_chat_reference")
    _need(file.get("id") == file_id and file.get("filename") == name and isinstance(file.get("user_id"), str), "unsupported_chat_reference")
    _need(meta.get("name") == name and meta.get("content_type") == "text/plain" and type(meta.get("size")) is int and meta["size"] == ref["size"], "unsupported_chat_reference")
    _need(file.get("data") == {} and meta.get("data") == {} and file.get("hash") is None, "unsupported_chat_reference")
    _need(type(file.get("created_at")) is int and type(file.get("updated_at")) is int, "unsupported_chat_reference")
    if "file_hash" in meta:
        _need(isinstance(meta["file_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", meta["file_hash"]), "unsupported_chat_reference")
    return file_id


def _graph_reader(raw, owner, title, db, state):
    graph = core._loads(raw, limit=core.MAX_OUTPUT)
    _need(isinstance(graph, dict) and isinstance(graph.get("history"), dict), "unsupported_chat_graph")
    history = graph["history"]
    nodes, flat = history.get("messages"), graph.get("messages")
    _need(isinstance(nodes, dict) and isinstance(flat, list), "unsupported_chat_graph")
    top = _refs(graph.get("files"))
    history_refs = [(key, node, _refs(node.get("files"))) for key, node in nodes.items() if isinstance(node, dict) and _refs(node.get("files"))]
    flat_refs = [(node, _refs(node.get("files"))) for node in flat if isinstance(node, dict) and _refs(node.get("files"))]
    _need(len(top) == len(history_refs) == len(flat_refs) == 1, "unsupported_chat_reference")
    history_id, history_node, href = history_refs[0]
    flat_node, fref = flat_refs[0]
    selected = history.get("currentId")
    _need(history_node.get("role") == flat_node.get("role") == "user" and history_node.get("id") == history_id and flat_node.get("id") == history_id, "unsupported_chat_reference")
    _need(isinstance(selected, str) and selected in nodes, "invalid_selected_message")
    ancestry, seen, cursor = [], set(), selected
    while cursor is not None:
        _need(isinstance(cursor, str) and cursor not in seen and cursor in nodes and isinstance(nodes[cursor], dict), "invalid_chat_graph")
        seen.add(cursor); ancestry.append(cursor); cursor = nodes[cursor].get("parentId")
    _need(history_id in ancestry and [x.get("id") if isinstance(x, dict) else None for x in flat] == list(reversed(ancestry)), "flat_chain_mismatch")
    ref = top[0]
    _need(href[0] == ref == fref[0], "unsupported_chat_reference")
    file_id = _reference_shape(ref)
    _need(file_id == state.get("file_id") or state.get("file_id") is None, "unsupported_chat_reference")
    row, public_meta, data, snapshot = _read_file(db, state["uploads_root"], file_id, owner)
    supplied = ref["file"]
    _need(supplied["id"] == row["id"] and supplied["user_id"] == owner and supplied["filename"] == row["filename"] and supplied["hash"] == row["hash"] and supplied["created_at"] == row["created_at"] and supplied["updated_at"] == row["updated_at"] and supplied["data"] == public_meta["data"] and supplied["meta"] == public_meta["meta"] and ref["size"] == public_meta["meta"]["size"], "attached_file_unavailable")
    state.update(file_id=file_id, file_row=row, source_file_metadata=public_meta, file_bytes=data, file_snapshot=snapshot)
    original = json.loads(json.dumps(ref, ensure_ascii=False))
    token_ref = dict(original, id=TOKEN, url="openatelier-attachment:" + TOKEN, file={"attachment_token": TOKEN})
    graph["files"] = []
    history_node["files"] = []
    flat_node["files"] = []
    validated, node_count, chain_count, selected = core._graph(core._dumps(graph).decode("utf-8"), owner, title)
    validated["files"] = [token_ref]
    validated["history"]["messages"][history_id]["files"] = [token_ref]
    for message in validated["messages"]:
        if message.get("id") == history_id:
            message["files"] = [token_ref]
            break
    else:
        raise core.Refusal("flat_chain_mismatch")
    return validated, node_count, chain_count, selected


def _root_path(root):
    try:
        path = Path(root).absolute()
        _need(".." not in path.parts, "unsafe_upload_root")
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            info = current.lstat()
            _need(not stat.S_ISLNK(info.st_mode), "unsafe_upload_root")
        _need(path.is_dir(), "unsafe_upload_root")
        return path
    except core.Refusal:
        raise
    except OSError:
        raise core.Refusal("unsafe_upload_root") from None


def _read_file(db, uploads_root, file_id, owner):
    schema = db.execute("SELECT type,sql FROM sqlite_master WHERE name='file'").fetchone()
    _need(schema is not None and schema["type"] == "table" and "VIRTUAL TABLE" not in (schema["sql"] or "").upper(), "unsupported_file_schema")
    columns = db.execute('PRAGMA table_info("file")').fetchall()
    _need({x["name"]: (x["type"] or "").upper() for x in columns} == FILE_COLUMNS, "unsupported_file_schema")
    identity = False
    for index in db.execute('PRAGMA index_list("file")'):
        if index["unique"] and not index["partial"]:
            quoted = '"' + index["name"].replace('"', '""') + '"'
            identity |= [x["name"] for x in db.execute("PRAGMA index_info(" + quoted + ")")] == ["id"]
    _need(identity, "ambiguous_file_identity")
    matches = db.execute('SELECT id,user_id,filename,path,hash,meta,data,created_at,updated_at,access_control FROM "file" WHERE id=?', (file_id,)).fetchmany(2)
    _need(len(matches) == 1, "attached_file_unavailable")
    row = dict(matches[0])
    _need(row["id"] == file_id and row["user_id"] == owner and row["hash"] is None, "attached_file_unavailable")
    _need(isinstance(row["filename"], str) and row["filename"] not in {"", ".", ".."} and Path(row["filename"]).name == row["filename"], "unsafe_upload_path")
    _need(not any(c in row["filename"] for c in '<>:"/\\|?*;') and not any(ord(c) < 32 or ord(c) == 127 for c in row["filename"]), "unsafe_upload_path")
    basename = file_id + "_" + row["filename"]
    stored_path = row["path"]
    _need(stored_path in (basename, "/app/backend/data/uploads/" + basename), "unsafe_upload_path")
    meta = core._loads(row["meta"], "invalid_file_metadata", 2048)
    data_meta = core._loads(row["data"], "invalid_file_data", 2048)
    acl = core._loads(row["access_control"], "invalid_file_access", 2048) if row["access_control"] is not None else None
    _need(isinstance(meta, dict) and set(meta) in ({"name", "content_type", "size", "data"}, {"name", "content_type", "size", "data", "file_hash"}), "unsupported_file_metadata")
    _need(meta.get("name") == row["filename"] and meta.get("content_type") == "text/plain" and type(meta.get("size")) is int and meta.get("data") == {} and data_meta == {} and acl in (None, {}), "attached_file_unavailable")
    if "file_hash" in meta:
        _need(isinstance(meta["file_hash"], str) and re.fullmatch(r"[0-9a-f]{64}", meta["file_hash"]), "invalid_file_hash")
    _need(type(row["created_at"]) is int and type(row["updated_at"]) is int and row["created_at"] >= 0 and row["updated_at"] >= 0, "invalid_file_timestamp")
    _need(1 <= meta["size"] <= MAX_FILE, "attached_file_limit")
    root = _root_path(uploads_root)
    path = root / basename
    before = path.lstat()
    _need(stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode) and before.st_size == meta["size"], "attached_file_unavailable")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        _need(stat.S_ISREG(opened.st_mode) and (before.st_dev, before.st_ino) == (opened.st_dev, opened.st_ino), "attached_file_changed")
        content = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
    after_path = path.lstat()
    _need(len(content) == meta["size"] and len(content) <= MAX_FILE and (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (after_path.st_dev, after_path.st_ino, after_path.st_size, after_path.st_mtime_ns), "attached_file_changed")
    try:
        content.decode("utf-8", "strict")
    except UnicodeError:
        raise core.Refusal("attached_file_not_utf8")
    digest = hashlib.sha256(content).hexdigest()
    _need("file_hash" not in meta or meta["file_hash"] == digest, "attached_file_hash_mismatch")
    public_meta = {"created_at": row["created_at"], "data": data_meta, "filename": row["filename"], "hash": row["hash"], "meta": meta, "updated_at": row["updated_at"]}
    return row, public_meta, content, (str(path), before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, digest)


def _bundle(source, uploads_root, owner, chat_id, started):
    state = {"uploads_root": uploads_root, "file_id": None}
    chat_data, receipt = core._read(source, owner, chat_id, started, graph_reader=lambda raw, who, title, db: _graph_reader(raw, who, title, db, state))
    _need(state.get("file_bytes") is not None, "attached_file_missing")
    chat_import = core._loads(chat_data)
    manifest = {"format": FORMAT, "chat_import": chat_import, "attachment": {"token": TOKEN, "member": MEMBER, "filename": state["file_row"]["filename"], "content_type": "text/plain", "bytes": len(state["file_bytes"]), "sha256": state["file_snapshot"][5], "source_file_metadata": state["source_file_metadata"]}}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8", "strict")
    _need(len(manifest_bytes) <= core.MAX_OUTPUT, "manifest_limit")
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
        for name, payload in (("manifest.json", manifest_bytes), (MEMBER, state["file_bytes"])):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload)
    output = stream.getvalue()
    _need(len(output) <= MAX_BUNDLE, "bundle_limit")
    _need(time.monotonic() - started <= core.DEADLINE, "deadline_exceeded")
    def prepublish():
        _need(time.monotonic() - started <= core.DEADLINE, "deadline_exceeded")
        expected = state["file_snapshot"]
        root = _root_path(uploads_root)
        path = Path(expected[0])
        _need(path.parent == root, "attached_file_changed")
        before = path.lstat()
        _need(stat.S_ISREG(before.st_mode) and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == expected[1:5], "attached_file_changed")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            current = stream.read(MAX_FILE + 1)
            after = os.fstat(stream.fileno())
        after_path = path.lstat()
        _need((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino) == (after_path.st_dev, after_path.st_ino) and (after.st_size, after.st_mtime_ns) == expected[3:5] == (after_path.st_size, after_path.st_mtime_ns), "attached_file_changed")
        _need(current == state["file_bytes"] and hashlib.sha256(current).hexdigest() == expected[5], "attached_file_changed")
        _need(time.monotonic() - started <= core.DEADLINE, "deadline_exceeded")
    receipt.update({"bundle_sha256": hashlib.sha256(output).hexdigest(), "bundle_bytes": len(output), "attachment_bytes": len(state["file_bytes"]), "attachment_sha256": state["file_snapshot"][5], "format": FORMAT})
    return output, receipt, prepublish


def _parser(argv):
    parser = core.Parser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "export"):
        sub = commands.add_parser(command)
        for arg in ("source", "uploads-root", "owner-id", "chat-id"):
            sub.add_argument("--" + arg, required=True)
        if command == "export":
            sub.add_argument("--output", required=True)
    return parser.parse_args(argv)


def build_bundle(source, uploads_root, owner_id, chat_id):
    """Return the deterministic archive bytes and a path-free receipt."""
    started = time.monotonic()
    _need(all(isinstance(x, str) and 1 <= len(x) <= 255 and not any(ord(c) < 32 or ord(c) == 127 for c in x) for x in (owner_id, chat_id)), "invalid_selection")
    return _bundle(source, uploads_root, owner_id, chat_id, started)[:2]


def main(argv=None):
    started = time.monotonic()
    try:
        args = _parser(argv)
        output, receipt, verify = _bundle(args.source, args.uploads_root, args.owner_id, args.chat_id, started)
        if args.command == "export":
            core._publish(args.output, args.source, receipt["source_sha256"], output, started, prepublish=verify)
        receipt.update({"artifact_written": args.command == "export", "elapsed_ms": int((time.monotonic() - started) * 1000)})
        result, code = {"ok": True, "command": args.command, "result": receipt}, 0
    except core.Refusal as error:
        result, code = {"ok": False, "reason": error.reason}, 2
    except SystemExit as error:
        if error.code == 0: return 0
        result, code = {"ok": False, "reason": "invalid_arguments"}, 2
    except Exception:
        result, code = {"ok": False, "reason": "bundle_failed"}, 2
    print(json.dumps(result, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
