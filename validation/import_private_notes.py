# SPDX-License-Identifier: BSD-3-Clause
"""Preview or copy a narrowly supported private-notes dataset between SQLite files."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time
from urllib.parse import quote


MAX_DB = 8 * 1024 * 1024
MAX_BUNDLE = 32 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
MAX_NOTES = 256
MAX_PINS = 512
MAX_JSON_TEXT = 128 * 1024
MAX_NOTE_JSON = 32 * 1024
MAX_JSON_TOTAL = 8 * 1024 * 1024
DEADLINE_SECONDS = 30.0
PROVISIONAL_SECONDS = 2.0
MAX_IDENTIFIER_BYTES = 1024
NOTE_TABLE = "openatelier_private_note"
NOTE_COLUMNS = tuple(zip(("id", "user_id", "title", "data", "meta", "created_at", "updated_at", "is_pinned"),
                         ("TEXT", "TEXT", "TEXT", "TEXT", "TEXT", "INTEGER", "INTEGER", "INTEGER"),
                         (0, 1, 1, 0, 0, 1, 1, 1), (1, 0, 0, 0, 0, 0, 0, 0)))
SOURCE_COLUMNS = {
    "note": tuple(zip(("id", "user_id", "title", "data", "meta", "created_at", "updated_at"),
                      ("TEXT", "TEXT", "TEXT", "JSON", "JSON", "BIGINT", "BIGINT"),
                      (1, 0, 0, 0, 0, 0, 0), (1, 0, 0, 0, 0, 0, 0))),
    "pinned_note": tuple(zip(("id", "user_id", "note_id", "created_at"),
                             ("TEXT", "TEXT", "TEXT", "BIGINT"), (1, 1, 1, 1), (1, 0, 0, 0))),
    "access_grant": tuple(zip(("id", "resource_type", "resource_id", "principal_type", "principal_id", "permission", "created_at"),
                              ("TEXT",) * 6 + ("BIGINT",), (1,) * 7, (1,) + (0,) * 6)),
}


class Refusal(Exception):
    def __init__(self, code, published=False, output_hash=None, output_state="published_unconfirmed"):
        self.code = code
        self.published = published
        self.output_hash = output_hash
        self.output_state = output_state


def _check_time(started):
    if time.monotonic() - started > DEADLINE_SECONDS:
        raise Refusal("resource_limit")


def _hash_file(path, started, limit=MAX_DB):
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refusal("unsafe_input")
        while True:
            _check_time(started)
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise Refusal("resource_limit")
            digest.update(chunk)
    return total, digest.hexdigest()


def _safe_existing_file(value, started):
    path = Path(value)
    try:
        if not path.is_absolute():
            raise Refusal("unsafe_input")
        for parent in reversed(path.parents):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise Refusal("unsafe_input")
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DB:
            raise Refusal("unsafe_input" if not stat.S_ISREG(info.st_mode) else "resource_limit")
        size, digest = _hash_file(path, started)
        after = path.lstat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise Refusal("source_changed")
        return path, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns), (size, digest)
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("unsafe_input") from None


def _bundle_snapshot(path, started):
    result = []
    total = 0
    for label, item in (("main", path), ("wal", Path(str(path) + "-wal")), ("shm", Path(str(path) + "-shm"))):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if label == "main":
                raise Refusal("unsafe_input")
            continue
        except OSError:
            raise Refusal("unsafe_input") from None
        if not stat.S_ISREG(info.st_mode):
            raise Refusal("unsafe_input")
        size, digest = _hash_file(item, started)
        after = item.lstat()
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise Refusal("source_changed")
        total += size
        if total > MAX_BUNDLE:
            raise Refusal("resource_limit")
        result.append((label, item, (info.st_dev, info.st_ino, size, info.st_mtime_ns), digest))
    journal = Path(str(path) + "-journal")
    if journal.exists() or journal.is_symlink():
        raise Refusal("sidecar_unsupported")
    return result


def _assert_bundle_unchanged(entries, started):
    current = _bundle_snapshot(entries[0][1], started)
    if {row[0] for row in current} != {row[0] for row in entries}:
        raise Refusal("source_changed")
    expected = {label: (path, identity, digest) for label, path, identity, digest in entries}
    for label, path, identity, digest in current:
        old_path, old_identity, old_digest = expected[label]
        try:
            info = path.lstat()
            size, actual = _hash_file(path, started)
        except (OSError, Refusal):
            raise Refusal("source_changed") from None
        if path != old_path or (info.st_dev, info.st_ino, size, info.st_mtime_ns) != old_identity or actual != old_digest:
            raise Refusal("source_changed")


def _sqlite_uri(path, mode): return "file:" + quote(str(path), safe="/") + "?mode=" + mode


def _connect(path, mode, started):
    try:
        db = sqlite3.connect(_sqlite_uri(path, mode), uri=True, timeout=1.0)
        db.execute("PRAGMA busy_timeout=1000")
        db.execute("PRAGMA trusted_schema=OFF")
        if mode == "ro":
            db.execute("PRAGMA query_only=ON")
        db.set_progress_handler(lambda: int(time.monotonic() - started > DEADLINE_SECONDS), 500)
        return db
    except (sqlite3.Error, ValueError, OSError):
        raise Refusal("sqlite_unavailable") from None


def _table_names(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(db, table):
    if table not in _table_names(db):
        raise Refusal("unsupported_schema")
    return tuple((r[1], r[2].upper(), r[3], r[5]) for r in db.execute('PRAGMA table_info("' + table.replace('"', '""') + '")'))


def _check_source_schema(db):
    if any(_columns(db, table) != expected for table, expected in SOURCE_COLUMNS.items()): raise Refusal("unsupported_schema")
    for row in db.execute("SELECT name,tbl_name FROM sqlite_master WHERE type='trigger'"):
        if row[1] in SOURCE_COLUMNS:
            raise Refusal("unsupported_schema")
    integrity = db.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        raise Refusal("corrupt_source")


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite")


def _check_json_value(value):
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 64: raise Refusal("unsupported_json")
        if item is None or type(item) in (bool, int): continue
        if type(item) is float:
            if not (float("-inf") < item < float("inf")): raise Refusal("unsupported_json")
        elif isinstance(item, str):
            try: item.encode("utf-8")
            except UnicodeError: raise Refusal("unsupported_json") from None
        elif type(item) is list: stack.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            for key, child in item.items():
                if not isinstance(key, str): raise Refusal("unsupported_json")
                stack.extend(((key, depth + 1), (child, depth + 1)))
        else: raise Refusal("unsupported_json")


def _parse_json_text(raw, allow_dict):
    if raw is None: return None, 0
    if type(raw) is not str: raise Refusal("unsupported_json")
    try:
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_JSON_TEXT:
            raise Refusal("resource_limit")
        value = json.loads(raw, object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
        _check_json_value(value)
    except Refusal:
        raise
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise Refusal("unsupported_json") from None
    if value is not None and (not allow_dict or type(value) is not dict):
        raise Refusal("unsupported_json")
    return value, len(encoded)


def _positive_timestamp(value):
    return type(value) is int and 0 <= value <= 0x7FFFFFFFFFFFFFFF


def _identity(db, user_id):
    if not {"id", "email", "role"}.issubset({r[1] for r in db.execute('PRAGMA table_info("user")')}): raise Refusal("unsupported_schema")
    rows = db.execute('SELECT id,email,role FROM "user" WHERE id=? LIMIT 2', (user_id,)).fetchall()
    if len(rows) != 1: raise Refusal("owner_mismatch")
    uid, email, role = rows[0]
    if not all(isinstance(v, str) and v for v in (uid, email, role)): raise Refusal("owner_mismatch")
    if role not in ("user", "admin"): raise Refusal("owner_unsupported")
    return uid, email, role


def _target_owner_matches(source_db, target_db, owner_id):
    source = _identity(source_db, owner_id)
    if source != _identity(target_db, owner_id): raise Refusal("owner_mismatch")


def _check_user_schema(db):
    if not {"id", "email", "role"}.issubset({r[1] for r in db.execute('PRAGMA table_info("user")')}):
        raise Refusal("unsupported_schema")


def _read_notes(db, target_db):
    _check_source_schema(db)
    try:
        if target_db.execute("PRAGMA integrity_check").fetchone() != ("ok",): raise Refusal("corrupt_target")
    except sqlite3.Error:
        raise Refusal("corrupt_target") from None
    rows = db.execute(
        "SELECT id,user_id,title,data,meta,created_at,updated_at FROM note ORDER BY id LIMIT ?",
        (MAX_NOTES + 1,),
    ).fetchall()
    if len(rows) > MAX_NOTES:
        raise Refusal("resource_limit")
    notes = {}
    aggregate_json = 0
    for row in rows:
        note_id, user_id, title, data_raw, meta_raw, created, updated = row
        if not _valid_identifier(note_id) or not _valid_identifier(user_id) or not isinstance(title, str): raise Refusal("malformed_note")
        try:
            title_size = len(title.encode("utf-8"))
        except UnicodeError:
            raise Refusal("malformed_note") from None
        if title_size > 8192 or not _positive_timestamp(created) or not _positive_timestamp(updated): raise Refusal("malformed_note")
        data, data_size = _parse_json_text(data_raw, True)
        meta, meta_size = _parse_json_text(meta_raw, True)
        semantic = json.dumps({"title": title, "data": data, "meta": meta}, ensure_ascii=False,
                              allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(semantic) > MAX_NOTE_JSON: raise Refusal("resource_limit")
        aggregate_json += data_size + meta_size
        if aggregate_json > MAX_JSON_TOTAL: raise Refusal("resource_limit")
        if note_id in notes: raise Refusal("duplicate_note")
        _target_owner_matches(db, target_db, user_id)
        notes[note_id] = dict(id=note_id, user_id=user_id, title=title, data=data_raw, meta=meta_raw,
                              created_at=created, updated_at=updated, is_pinned=0)
    return notes


def _apply_pins(db, notes):
    pins = db.execute("SELECT id,user_id,note_id,created_at FROM pinned_note ORDER BY id LIMIT ?", (MAX_PINS + 1,)).fetchall()
    if len(pins) > MAX_PINS: raise Refusal("resource_limit")
    ids, pairs = set(), set()
    for pin_id, user_id, note_id, created_at in pins:
        if not _valid_identifier(pin_id) or pin_id in ids: raise Refusal("malformed_pin")
        if not _valid_identifier(user_id) or not _valid_identifier(note_id) or not _positive_timestamp(created_at): raise Refusal("malformed_pin")
        pair = (user_id, note_id)
        if pair in pairs: raise Refusal("duplicate_pin")
        note = notes.get(note_id)
        if note is None or note["user_id"] != user_id: raise Refusal("orphan_pin")
        ids.add(pin_id)
        pairs.add(pair)
        note["is_pinned"] = 1
    if db.execute("SELECT 1 FROM access_grant WHERE typeof(resource_type)!='text' OR resource_type='note' LIMIT 1").fetchone():
        raise Refusal("note_grants_unsupported")


def _valid_identifier(value):
    if not isinstance(value, str) or not value: return False
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        return False
    return len(raw) <= MAX_IDENTIFIER_BYTES and all(ord(c) >= 32 and not 127 <= ord(c) <= 159 for c in value)


def _target_notes_state(db):
    obj = db.execute("SELECT type FROM sqlite_master WHERE name=?", (NOTE_TABLE,)).fetchone()
    index_owner = db.execute("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", ("openatelier_private_note_owner_time",)).fetchone()
    if obj is None:
        if index_owner: raise Refusal("target_schema_collision")
        return False
    if obj[0] != "table": raise Refusal("target_schema_collision")
    if index_owner and index_owner[0] != NOTE_TABLE: raise Refusal("target_schema_collision")
    actual = _columns(db, NOTE_TABLE)
    if actual != NOTE_COLUMNS:
        raise Refusal("target_schema_collision")
    count = db.execute('SELECT count(*) FROM "' + NOTE_TABLE + '"').fetchone()[0]
    if count != 0:
        raise Refusal("target_notes_not_empty")
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=? LIMIT 1",
        (NOTE_TABLE,),
    ).fetchone():
        raise Refusal("target_schema_collision")
    indexes = list(db.execute('PRAGMA index_list("' + NOTE_TABLE + '")'))
    allowed = {"openatelier_private_note_owner_time", "sqlite_autoindex_openatelier_private_note_1"}
    if any(index[1] not in allowed for index in indexes):
        raise Refusal("target_schema_collision")
    found = False
    for index in indexes:
        if index[1] == "openatelier_private_note_owner_time":
            found = True
            info = tuple(row[2] for row in db.execute(
                'PRAGMA index_info("openatelier_private_note_owner_time")'
            ))
            if info != ("user_id", "updated_at", "id") or index[2] or (len(index) > 4 and index[4]):
                raise Refusal("target_schema_collision")
            return True
    if not found: raise Refusal("target_schema_collision")
    return True


def _open_source_copy(source_path, entries, scratch_dir, started):
    copied = scratch_dir / "source.db"
    for label, path, _, digest in entries:
        destination = copied if label == "main" else Path(str(copied) + ("-wal" if label == "wal" else "-shm"))
        with path.open("rb") as source, destination.open("xb") as target:
            os.chmod(destination, 0o600)
            copied_hash = hashlib.sha256()
            count = 0
            while True:
                _check_time(started)
                data = source.read(64 * 1024)
                if not data:
                    break
                count += len(data)
                if count > MAX_DB:
                    raise Refusal("resource_limit")
                copied_hash.update(data)
                target.write(data)
            target.flush()
            os.fsync(target.fileno())
        if copied_hash.hexdigest() != digest:
            raise Refusal("source_changed")
    return copied


def _path_identity(path):
    try:
        info = path.lstat()
        return info.st_dev, info.st_ino
    except OSError:
        return None


def _assert_target_unchanged(path, identity, file_info, started):
    current = _bundle_snapshot(path, started)
    if len(current) != 1 or current[0][2] != identity or (current[0][2][2], current[0][3]) != file_info:
        raise Refusal("target_changed")


def _output_path(value, source_path, target_path):
    path = Path(value)
    try:
        if not path.is_absolute() or path.exists() or path.is_symlink():
            raise Refusal("output_exists_or_unsafe")
        for parent in reversed(path.parents):
            if not stat.S_ISDIR(parent.lstat().st_mode):
                raise Refusal("output_exists_or_unsafe")
        if path == source_path or path == target_path or path.parent == source_path or path.parent == target_path:
            raise Refusal("output_overlaps_input")
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise Refusal("output_exists_or_unsafe")
        return path
    except Refusal:
        raise
    except OSError:
        raise Refusal("output_exists_or_unsafe") from None


def _rss_bytes():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if os.uname().sysname == "Darwin" else value * 1024)
    except (ImportError, AttributeError, OSError):
        return None


def run(mode, source_db, target_db, new_target_db=None):
    started = time.monotonic()
    source_path, _, _ = _safe_existing_file(source_db, started)
    target_path, target_identity, target_file = _safe_existing_file(target_db, started)
    if target_file[0] > MAX_DB:
        raise Refusal("resource_limit")
    if source_path == target_path or _path_identity(source_path) == _path_identity(target_path):
        raise Refusal("input_collision")
    target_sidecars = _bundle_snapshot(target_path, started)
    if len(target_sidecars) != 1:
        raise Refusal("target_sidecars_unsupported")
    source_entries = _bundle_snapshot(source_path, started)
    bundle_bytes = sum(item[2][2] for item in source_entries)
    if bundle_bytes > MAX_BUNDLE:
        raise Refusal("resource_limit")
    bundle_hash = hashlib.sha256("\n".join(r[0] + ":" + r[3] for r in source_entries).encode("ascii")).hexdigest()
    target_hash = target_file[1]
    if mode not in ("preview", "convert"): raise Refusal("invalid_arguments")
    if mode == "convert" and not new_target_db:
        raise Refusal("invalid_arguments")
    output = _output_path(new_target_db, source_path, target_path) if mode == "convert" else None
    notes_count = pinned_count = 0
    with tempfile.TemporaryDirectory(prefix="private-notes-import-") as private:
        scratch = Path(private)
        os.chmod(scratch, 0o700)
        copied_source = _open_source_copy(source_path, source_entries, scratch, started)
        copied_target = scratch / "target.db"
        with target_path.open("rb") as source, copied_target.open("xb") as target:
            os.chmod(copied_target, 0o600)
            shutil.copyfileobj(source, target, 65536)
            target.flush()
            os.fsync(target.fileno())
        if _hash_file(copied_target, started) != target_file:
            raise Refusal("target_changed")
        source_conn = _connect(copied_source, "ro", started)
        target_conn = _connect(copied_target, "ro", started)
        try:
            _check_user_schema(source_conn)
            _check_user_schema(target_conn)
            notes = _read_notes(source_conn, target_conn)
            _apply_pins(source_conn, notes)
            notes_count = len(notes)
            pinned_count = sum(row["is_pinned"] for row in notes.values())
            state = _target_notes_state(target_conn)
        finally:
            source_conn.close()
            target_conn.close()
        if mode == "convert":
            staged = sqlite3.connect(str(copied_target), timeout=1.0)
            try:
                staged.execute("PRAGMA trusted_schema=OFF")
                staged.execute("PRAGMA journal_mode=DELETE")
                staged.execute("PRAGMA synchronous=FULL")
                staged.execute("BEGIN IMMEDIATE")
                if not state:
                    staged.execute(
                        "CREATE TABLE openatelier_private_note (id TEXT PRIMARY KEY,"
                        "user_id TEXT NOT NULL,title TEXT NOT NULL,data TEXT,meta TEXT,"
                        "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
                        "is_pinned INTEGER NOT NULL DEFAULT 0)"
                    )
                staged.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_private_note_owner_time "
                    "ON openatelier_private_note(user_id,updated_at DESC,id DESC)"
                )
                staged.executemany(
                    "INSERT INTO openatelier_private_note "
                    "(id,user_id,title,data,meta,created_at,updated_at,is_pinned) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [(r["id"], r["user_id"], r["title"], r["data"], r["meta"],
                      r["created_at"], r["updated_at"], r["is_pinned"]) for r in notes.values()],
                )
                staged.commit()
                if staged.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise Refusal("corrupt_output")
            except Exception:
                staged.rollback()
                raise
            finally:
                staged.close()
            if any(Path(str(copied_target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                raise Refusal("target_sidecars_unsupported")
            if copied_target.stat().st_size > MAX_FILE:
                raise Refusal("resource_limit")
            fd, name = tempfile.mkstemp(prefix="." + output.name + ".private-notes-", dir=output.parent)
            temp = Path(name)
            temp_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
            linked = None
            confirmed = False
            try:
                with copied_target.open("rb") as source, os.fdopen(fd, "wb") as dest:
                    shutil.copyfileobj(source, dest, 65536)
                    dest.flush()
                    os.fsync(dest.fileno())
                if temp.stat().st_size > MAX_FILE:
                    raise Refusal("resource_limit")
                if _path_identity(temp) != temp_identity: raise Refusal("output_publish_failed")
                output_hash = _hash_file(temp, started, MAX_FILE)[1]
                shutil.rmtree(scratch)
                _assert_bundle_unchanged(source_entries, started)
                _assert_target_unchanged(target_path, target_identity, target_file, started)
                _check_time(started)
                expected = temp_identity
                os.link(temp, output)
                linked = expected
                if _path_identity(output) != expected:
                    raise Refusal("output_publish_failed")
                fd = os.open(output.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if _path_identity(output) != expected: raise Refusal("output_publish_failed")
                confirmed = True
                linked = None
            except FileExistsError:
                raise Refusal("output_exists_or_unsafe") from None
            except OSError:
                if linked and _path_identity(output) == linked:
                    try:
                        output.unlink()
                        linked = None
                    except OSError:
                        raise Refusal("published_unconfirmed", True, output_hash) from None
                raise Refusal("publication_failed") from None
            finally:
                if linked and _path_identity(output) == linked:
                    try: output.unlink()
                    except OSError: raise Refusal("published_unconfirmed", True, output_hash) from None
                if temp.exists():
                    try: temp.unlink()
                    except OSError:
                        if confirmed: raise Refusal("published_cleanup_incomplete", True, output_hash, "published") from None
                        raise Refusal("scratch_cleanup_failed") from None
    if mode == "preview":
        _assert_bundle_unchanged(source_entries, started)
        _assert_target_unchanged(target_path, target_identity, target_file, started)
    report = {
        "ok": True, "mode": mode, "eligible": True,
        "reason": "supported_private_notes_subset",
        "note_count": notes_count, "pinned_note_count": pinned_count,
        "source_bundle_sha256": bundle_hash, "target_input_sha256": target_hash,
        "output_sha256": output_hash if mode == "convert" else None,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "within_provisional_2s_target": time.monotonic() - started <= PROVISIONAL_SECONDS,
        "peak_rss_bytes": _rss_bytes(), "cutover": False,
        "limitations": ["No uploads or path rewriting", "Only private note data and same-owner pins are mapped"],
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="import_private_notes.py")
    sub = parser.add_subparsers(dest="mode", required=True)
    preview = sub.add_parser("preview")
    convert = sub.add_parser("convert")
    for command in (preview, convert):
        command.add_argument("--source-db", required=True)
        command.add_argument("--target-db", required=True)
    convert.add_argument("--new-target-db", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.mode, args.source_db, args.target_db, getattr(args, "new_target_db", None))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Refusal as error:
        result = {"ok": False, "eligible": False, "reason": error.code, "cutover": False}
        if error.published:
            result.update(output_state=error.output_state, output_sha256=error.output_hash)
        print(json.dumps(result,
                         sort_keys=True, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "eligible": False, "reason": "conversion_unavailable", "cutover": False},
                         sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
