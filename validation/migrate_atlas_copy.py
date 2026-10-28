#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Coordinate a copy-only core migration and private-notes merge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import shutil
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import import_private_notes as notes
import migrate_sqlite as core

MAX_UPLOAD_FILES = 4096
CHUNK = 64 * 1024
FLAGS = ("include_private_chat_metadata", "include_named_tool_configuration", "include_user_profile_fields",
         "include_api_keys", "include_api_key_configuration", "include_openai_configuration",
         "include_user_permissions", "include_function_history")


class Refusal(Exception):
    def __init__(self, code): self.code = code


def _hash(path):
    digest = hashlib.sha256()
    path = Path(path)
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise Refusal("unsafe_input")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise Refusal("source_changed")
        while True:
            block = os.read(fd, CHUNK)
            if not block: break
            digest.update(block)
        after = os.fstat(fd)
        current = path.lstat()
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or (current.st_dev, current.st_ino) != (after.st_dev, after.st_ino):
            raise Refusal("source_changed")
    finally:
        os.close(fd)
    return digest.hexdigest()


def _copy_regular(source, destination, limit):
    source, destination = Path(source), Path(destination)
    before = source.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > limit: raise Refusal("unsafe_input")
    source_hash = _hash(source)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    current = source.lstat()
    size, copied_hash = destination.stat().st_size, _hash(destination)
    if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) or copied_hash != source_hash or size != before.st_size: raise Refusal("source_changed")
    return size, copied_hash


def _upload_manifest(root):
    root = Path(root)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise Refusal("unsafe_upload_root")
    entries = []
    total = 0
    try:
        for entry in sorted(os.scandir(root), key=lambda item: item.name):
            name_bytes, item = entry.name.encode("utf-8"), Path(entry.path)
            file_info = item.lstat()
            if not name_bytes or len(name_bytes) > 1024 or stat.S_ISLNK(file_info.st_mode) or not stat.S_ISREG(file_info.st_mode):
                raise Refusal("unsupported_upload_tree")
            size = file_info.st_size
            total += size
            if len(entries) >= MAX_UPLOAD_FILES or total > core.MAX_INPUT_BYTES:
                raise Refusal("resource_limit")
            entries.append((entry.name, size, _hash(item), (file_info.st_dev, file_info.st_ino, file_info.st_mtime_ns)))
    except UnicodeError:
        raise Refusal("unsupported_upload_tree") from None
    digest = hashlib.sha256()
    for name, size, file_hash, _ in entries:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big") + encoded + size.to_bytes(8, "big") + bytes.fromhex(file_hash))
    return entries, total, digest.hexdigest()


def _copy_uploads(source_root, destination_root, expected):
    start, total, digest = _upload_manifest(source_root)
    if start != expected[0] or digest != expected[2]:
        raise Refusal("source_changed")
    def copy_private(src, dst):
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        return dst
    shutil.copytree(source_root, destination_root, symlinks=True, copy_function=copy_private)
    copied, copied_total, copied_digest = _upload_manifest(destination_root)
    if ([(r[0], r[1], r[2]) for r in copied], copied_total, copied_digest) != ([(r[0], r[1], r[2]) for r in start], total, digest):
        raise Refusal("source_changed")
    end = _upload_manifest(source_root)
    if end != expected: raise Refusal("source_changed")
    return total, digest


def _new_directory(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.exists() or path.is_symlink():
        raise Refusal("output_exists_or_unsafe")
    try:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir(): raise Refusal("output_exists_or_unsafe")
        return parent / path.name
    except OSError:
        raise Refusal("output_exists_or_unsafe") from None


def _disjoint(outputs, inputs):
    paths = outputs + inputs
    if any(a != b and (a.is_relative_to(b) or b.is_relative_to(a))
           for i, a in enumerate(paths) for b in paths[i + 1:]):
        raise Refusal("path_overlap")


def _readonly(path):
    return sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro&immutable=1", uri=True)


def _note_snapshot(source_path):
    db = _readonly(source_path)
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        present = names.intersection(("note", "pinned_note"))
        if not present: return {}, None
        if present != {"note", "pinned_note"}:
            raise Refusal("notes_schema_unsupported")
        try:
            notes._check_source_schema(db)
            rows = notes._read_notes(db, db)
            notes._apply_pins(db, rows)
        except notes.Refusal as error:
            raise Refusal(error.code) from None
        schema = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        incoming = [table for (table,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            if table not in ("note", "pinned_note") and any(
                row[2] in ("note", "pinned_note") for row in db.execute(
                    f'PRAGMA foreign_key_list("{table.replace(chr(34), chr(34)*2)}")'))]
        if incoming:
            raise Refusal("notes_referenced_by_other_rows")
        return rows, schema
    finally:
        db.close()


def _trim_stage(stage_path, expected_schema, notes_map):
    db = sqlite3.connect(stage_path, timeout=2.0)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        before_schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        if before_schema != expected_schema:
            raise Refusal("stage_schema_mismatch")
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        before_counts = {table: db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0] for table in tables}
        if before_counts.get("note", 0) != len(notes_map):
            raise Refusal("stage_note_count_mismatch")
        db.execute("BEGIN IMMEDIATE")
        db.execute('DELETE FROM "pinned_note"')
        db.execute('DELETE FROM "note"')
        if db.execute('SELECT COUNT(*) FROM "note"').fetchone()[0] or db.execute('SELECT COUNT(*) FROM "pinned_note"').fetchone()[0]:
            raise Refusal("stage_trim_failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise Refusal("stage_foreign_key_check_failed")
        after_counts = {table: db.execute('SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"').fetchone()[0] for table in tables}
        for table in tables:
            expected = 0 if table in ("note", "pinned_note") else before_counts[table]
            if after_counts[table] != expected:
                raise Refusal("stage_unexpected_row_change")
        if db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall() != expected_schema:
            raise Refusal("stage_schema_changed")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _write_private(path, value):
    encoded = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())


def _verify_notes(target_path, expected):
    db = _readonly(target_path)
    try:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise Refusal("merged_target_corrupt")
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='openatelier_private_note'").fetchone()
        if not expected:
            if exists and db.execute("SELECT 1 FROM openatelier_private_note LIMIT 1").fetchone():
                raise Refusal("notes_readback_mismatch")
            return
        rows = db.execute(
            "SELECT id,user_id,title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note ORDER BY id"
        ).fetchall()
        actual = {row[0]: tuple(row[1:]) for row in rows}
        wanted = {key: tuple(row[k] for k in ("user_id", "title", "data", "meta", "created_at", "updated_at", "is_pinned"))
                  for key, row in expected.items()}
        if actual != wanted:
            raise Refusal("notes_readback_mismatch")
    except sqlite3.Error:
        raise Refusal("notes_readback_mismatch") from None
    finally:
        db.close()


def run(mode, source_db, uploads_root, template_path, workspace_dir, target_dir=None,
        runtime_data_root="/app/backend/data", include_history_sidecar=False, **options):
    if mode not in ("preview", "convert") or (mode == "convert") != bool(target_dir):
        raise Refusal("invalid_arguments")
    source = core._db_path(source_db, label="source database")
    template = core._db_path(template_path, label="old target template")
    uploads = Path(uploads_root)
    if uploads.is_symlink() or not uploads.is_dir():
        raise Refusal("unsafe_upload_root")
    workspace = _new_directory(workspace_dir)
    outputs = [workspace]
    target = _new_directory(target_dir) if target_dir else None
    if target is not None:
        outputs.append(target)
    _disjoint(outputs, [source, template, uploads.resolve(strict=True)])
    source_hash, template_hash = core._digest(source), core._digest(template)
    upload_before = _upload_manifest(uploads)
    if source.stat().st_size + upload_before[1] > core.MAX_INPUT_BYTES:
        raise Refusal("resource_limit")
    workspace.mkdir(mode=0o700)
    try:
        full_source = workspace / "source.sqlite"
        copied_db = _copy_regular(source, full_source, core.MAX_INPUT_BYTES)
        full_uploads = workspace / "uploads"
        copied_uploads = _copy_uploads(uploads, full_uploads, upload_before)
        template_copy = workspace / "old-template.sqlite"
        _copy_regular(template, template_copy, core.MAX_INPUT_BYTES)
        if core._digest(template_copy) != template_hash:
            raise Refusal("source_changed")
        notes_map, schema = _note_snapshot(full_source)
        note_count, pin_count = len(notes_map), sum(row["is_pinned"] for row in notes_map.values())
        _write_private(workspace / "input-manifest.json", {
            "source_sha256": source_hash, "template_sha256": template_hash,
            "uploads_manifest_sha256": upload_before[2], "upload_count": len(upload_before[0]),
            "upload_bytes": upload_before[1], "note_count": note_count, "pinned_note_count": pin_count,
            "full_source_copy_sha256": copied_db[1], "full_upload_copy_sha256": copied_uploads[1],
            "cutover": False,
        })
        stage = workspace / "core-staging.sqlite"
        _copy_regular(full_source, stage, core.MAX_INPUT_BYTES)
        if schema is not None:
            _trim_stage(stage, schema, notes_map)
        flags = {name: bool(options.get(name, False)) for name in FLAGS}
        core_report = core.preview(stage, full_uploads, template_copy, **flags)
        _write_private(workspace / "core-preview.json", core_report)
        summary = {
            "ok": not bool(core_report.get("fatal")), "mode": mode,
            "status": "preview_refused" if core_report.get("fatal") else "preview_partial" if core_report.get("blockers") else "preview_ready",
            "cutover": False, "source_sha256": source_hash,
            "uploads_manifest_sha256": upload_before[2], "note_count": note_count,
            "pinned_note_count": pin_count, "core_blocker_count": len(core_report.get("blockers", [])),
            "target_created": False,
        }
        if mode == "convert" and not summary["ok"]:
            raise Refusal("fatal_core_input")
        if mode == "convert":
            core_report = core.convert(stage, full_uploads, template_copy, target,
                                      runtime_data_root=runtime_data_root,
                                      include_history_sidecar=include_history_sidecar, **flags)
            app_db = target / "webui.db"
            if notes_map:
                merged = target / ".webui-notes-merged.db"
                note_report = notes.run("convert", full_source, app_db, merged)
                if not note_report.get("cutover") is False:
                    raise Refusal("notes_merge_failed")
                _verify_notes(merged, notes_map)
                _verify_notes(app_db, {})
                os.replace(merged, app_db)
                _verify_notes(app_db, notes_map)
                _write_private(workspace / "private-notes-convert.json", note_report)
            _write_private(workspace / "core-convert.json", core_report)
            summary.update(status="converted_copy_only", target_created=True,
                           target_database_sha256=core._digest(app_db))
            if core_report.get("blockers"):
                summary["status"] = "converted_partial_copy_only"
        if core._digest(source) != source_hash or core._digest(template) != template_hash or _upload_manifest(uploads) != upload_before:
            raise Refusal("original_changed")
        summary["originals_unchanged"] = True
        _write_private(workspace / "coordinator-report.json", summary)
        return summary
    except Exception as error:
        code = error.code if isinstance(error, (Refusal, core.MigrationError, notes.Refusal)) else "coordination_failed"
        if isinstance(error, core.MigrationError) and isinstance(getattr(error, "report", None), dict):
            try:
                _write_private(workspace / "core-failure.json", error.report)
            except OSError:
                pass
        failure = {"ok": False, "status": "failed_retained_workspace", "reason": code,
                   "cutover": False, "source_sha256": source_hash, "template_sha256": template_hash}
        try:
            _write_private(workspace / "failure.json", failure)
        except OSError:
            pass
        return failure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("preview", "convert"):
        command = sub.add_parser(mode)
        for arg in ("--source-db", "--uploads-root", "--empty-old-target-template", "--workspace-dir"):
            command.add_argument(arg, required=True)
        if mode == "convert":
            command.add_argument("--new-target-dir", required=True)
            command.add_argument("--runtime-data-root", default="/app/backend/data")
            command.add_argument("--include-history-sidecar", action="store_true")
        for name in FLAGS: command.add_argument("--" + name.replace("_", "-"), action="store_true")
    args = parser.parse_args(argv)
    options = {name: getattr(args, name) for name in FLAGS}
    try:
        result = run(args.mode, args.source_db, args.uploads_root, args.empty_old_target_template,
                     args.workspace_dir, getattr(args, "new_target_dir", None),
                     getattr(args, "runtime_data_root", "/app/backend/data"),
                     getattr(args, "include_history_sidecar", False), **options)
    except (Refusal, core.MigrationError, notes.Refusal, OSError, ValueError):
        result = {"ok": False, "status": "refused_input", "reason": "input_or_workspace_unavailable", "cutover": False}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
