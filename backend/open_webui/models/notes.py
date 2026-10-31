# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Small SQLite-only private-note store for the opt-in compatibility slice."""

import json
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


TABLE = "openatelier_private_note"
_COLUMNS = (
    ("id", "TEXT"),
    ("user_id", "TEXT"),
    ("title", "TEXT"),
    ("data", "TEXT"),
    ("meta", "TEXT"),
    ("created_at", "INTEGER"),
    ("updated_at", "INTEGER"),
    ("is_pinned", "INTEGER"),
)


class PrivateNotesUnavailable(RuntimeError):
    pass


def sqlite_path(database_url):
    if not isinstance(database_url, str) or not database_url.startswith("sqlite:///"):
        raise PrivateNotesUnavailable("Private notes require file-backed SQLite")
    raw = database_url[len("sqlite:///"):]
    if not raw or raw in (":memory:", "/:memory:") or "?" in raw or "#" in raw:
        raise PrivateNotesUnavailable("Private notes require file-backed SQLite")
    path = Path(raw)
    try:
        info = path.lstat()
    except OSError as error:
        raise PrivateNotesUnavailable("Private notes database is unavailable") from error
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode):
        raise PrivateNotesUnavailable("Private notes database is unavailable")
    for parent in path.parents:
        try:
            parent_info = parent.lstat()
        except OSError as error:
            raise PrivateNotesUnavailable("Private notes database is unavailable") from error
        if not stat.S_ISDIR(parent_info.st_mode):
            raise PrivateNotesUnavailable("Private notes database is unavailable")
    return path


class PrivateNotes:
    def __init__(self, database_url):
        self.path = sqlite_path(database_url)
        self._prepare()

    def _connect(self):
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        try:
            return sqlite3.connect(uri, uri=True, timeout=1.0)
        except sqlite3.Error as error:
            raise PrivateNotesUnavailable("Private notes database is unavailable") from error

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _prepare(self):
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS openatelier_private_note ("
                    "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, "
                    "data TEXT, meta TEXT, created_at INTEGER NOT NULL, "
                    "updated_at INTEGER NOT NULL, is_pinned INTEGER NOT NULL DEFAULT 0)"
                )
                details = list(db.execute("PRAGMA table_info(openatelier_private_note)"))
                actual = tuple((row[1], row[2].upper()) for row in details)
                if (
                    actual != _COLUMNS
                    or details[0][5] != 1
                    or any(row[5] for row in details[1:])
                    or tuple(row[3] for row in details)
                    != (0, 1, 1, 0, 0, 1, 1, 1)
                ):
                    raise PrivateNotesUnavailable("Private notes table schema is unsupported")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_private_note_owner_time "
                    "ON openatelier_private_note(user_id, updated_at DESC, id DESC)"
                )
                index = tuple(row[2] for row in db.execute(
                    "PRAGMA index_info(openatelier_private_note_owner_time)"
                ))
                index_row = next((row for row in db.execute(
                    "PRAGMA index_list(openatelier_private_note)"
                ) if row[1] == "openatelier_private_note_owner_time"), None)
                if index != ("user_id", "updated_at", "id") or index_row is None or index_row[2] != 0 or index_row[4] != 0:
                    raise PrivateNotesUnavailable("Private notes index is unsupported")
        except sqlite3.Error as error:
            raise PrivateNotesUnavailable("Private notes database is unavailable") from error

    @staticmethod
    def _row(row):
        if row is None:
            return None
        data = json.loads(row[3]) if row[3] is not None else None
        return {
            "id": row[0], "user_id": row[1], "title": row[2],
            "data": {} if data is None else data,
            "meta": json.loads(row[4]) if row[4] is not None else None,
            "created_at": row[5], "updated_at": row[6], "is_pinned": bool(row[7]),
            "access_grants": [],
        }

    def _one(self, db, note_id, user_id):
        return self._row(db.execute(
            "SELECT id,user_id,title,data,meta,created_at,updated_at,is_pinned "
            "FROM openatelier_private_note WHERE id=? AND user_id=?",
            (note_id, user_id),
        ).fetchone())

    def list_owned(self, user_id, limit, offset, pinned=None, query=None):
        where, params = "user_id=?", [user_id]
        if pinned is not None:
            where += " AND is_pinned=?"
            params.append(int(pinned))
        if query is not None:
            where += " AND title LIKE ? ESCAPE '\\'"
            params.append("%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        select = "SELECT id,user_id,title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE " + where
        with self._connection() as db:
            rows = db.execute(select + " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
            total = db.execute("SELECT count(*) FROM openatelier_private_note WHERE " + where, params).fetchone()[0]
        return [self._row(row) for row in rows], total

    def get_owned(self, note_id, user_id):
        with self._connection() as db:
            return self._one(db, note_id, user_id)

    def create(self, note):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO openatelier_private_note "
                "(id,user_id,title,data,meta,created_at,updated_at,is_pinned) "
                "VALUES(?,?,?,?,?,?,?,0)",
                (note["id"], note["user_id"], note["title"], note["data"], note["meta"], note["created_at"], note["updated_at"]),
            )
            return self._one(db, note["id"], note["user_id"])

    def update_owned(self, note_id, user_id, title, data, meta, updated_at, update_data=True, update_meta=True):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT data,meta FROM openatelier_private_note WHERE id=? AND user_id=?",
                (note_id, user_id),
            ).fetchone()
            if row is None:
                return None
            if type(update_data) is not bool or type(update_meta) is not bool:
                raise ValueError("Unsupported note update")
            raw_data = data if update_data else row[0]
            raw_meta = meta if update_meta else row[1]
            try:
                parsed_data = json.loads(raw_data) if raw_data is not None else None
                parsed_meta = json.loads(raw_meta) if raw_meta is not None else None
                if parsed_data is not None and type(parsed_data) is not dict or parsed_meta is not None and type(parsed_meta) is not dict:
                    raise ValueError("Unsupported note data")
                merged = json.dumps({"title": title, "data": parsed_data, "meta": parsed_meta},
                                    ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if len(merged.encode("utf-8")) > 32768:
                    raise ValueError("Note exceeds the supported size")
            except (TypeError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
                raise ValueError("Unsupported note data") from error
            fields, values = ["title=?", "updated_at=?"], [title, updated_at]
            if update_data:
                fields.append("data=?")
                values.append(data)
            if update_meta:
                fields.append("meta=?")
                values.append(meta)
            values.extend((note_id, user_id))
            cursor = db.execute("UPDATE openatelier_private_note SET " + ",".join(fields) + " WHERE id=? AND user_id=?", values)
            return self._one(db, note_id, user_id) if cursor.rowcount else None

    def toggle_pin(self, note_id, user_id):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT is_pinned FROM openatelier_private_note WHERE id=? AND user_id=?",
                (note_id, user_id),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE openatelier_private_note SET is_pinned=? WHERE id=? AND user_id=?",
                (0 if row[0] else 1, note_id, user_id),
            )
            return self._one(db, note_id, user_id)
