# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""SQLite automation metadata store for the measured compatibility contract."""

import json
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


class AutomationsUnavailable(RuntimeError):
    pass


def _sqlite_path(database_url):
    if not isinstance(database_url, str) or not database_url.startswith("sqlite:///"):
        raise AutomationsUnavailable("Automations require file-backed SQLite")
    raw = database_url[len("sqlite:///"):]
    if not raw or raw in (":memory:", "/:memory:") or "?" in raw or "#" in raw:
        raise AutomationsUnavailable("Automations require file-backed SQLite")
    path = Path(raw)
    try:
        info = path.lstat()
    except OSError as error:
        raise AutomationsUnavailable("Automation database is unavailable") from error
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode):
        raise AutomationsUnavailable("Automation database is unavailable")
    for parent in path.parents:
        try:
            parent_info = parent.lstat()
        except OSError as error:
            raise AutomationsUnavailable("Automation database is unavailable") from error
        if not stat.S_ISDIR(parent_info.st_mode):
            raise AutomationsUnavailable("Automation database is unavailable")
    return path


class Automations:
    """Own prefixed tables without changing inherited migration state."""

    def __init__(self, database_url):
        self.path = _sqlite_path(database_url)
        self._prepare()

    def _connect(self):
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        try:
            return sqlite3.connect(uri, uri=True, timeout=1.0)
        except sqlite3.Error as error:
            raise AutomationsUnavailable("Automation database is unavailable") from error

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
                    "CREATE TABLE IF NOT EXISTS openatelier_automation ("
                    "id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,folder_id TEXT,"
                    "data TEXT NOT NULL,meta TEXT,is_active INTEGER NOT NULL DEFAULT 1,"
                    "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
                    "last_run_at INTEGER,next_run_at INTEGER)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_automation_owner_time "
                    "ON openatelier_automation(user_id,updated_at DESC,id DESC)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS openatelier_automation_run ("
                    "id TEXT PRIMARY KEY,automation_id TEXT NOT NULL,status TEXT NOT NULL,"
                    "chat_id TEXT,error TEXT,created_at INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_automation_run_lookup "
                    "ON openatelier_automation_run(automation_id,created_at DESC,id DESC)"
                )
                expected = {
                    "openatelier_automation": (
                        ("id", "TEXT"), ("user_id", "TEXT"), ("name", "TEXT"),
                        ("folder_id", "TEXT"), ("data", "TEXT"), ("meta", "TEXT"),
                        ("is_active", "INTEGER"), ("created_at", "INTEGER"),
                        ("updated_at", "INTEGER"), ("last_run_at", "INTEGER"),
                        ("next_run_at", "INTEGER"),
                    ),
                    "openatelier_automation_run": (
                        ("id", "TEXT"), ("automation_id", "TEXT"), ("status", "TEXT"),
                        ("chat_id", "TEXT"), ("error", "TEXT"), ("created_at", "INTEGER"),
                    ),
                }
                for table, columns in expected.items():
                    actual = tuple((row[1], row[2].upper()) for row in db.execute(f"PRAGMA table_info({table})"))
                    if actual != columns:
                        raise AutomationsUnavailable("Automation table schema is unsupported")
        except sqlite3.Error as error:
            raise AutomationsUnavailable("Automation database is unavailable") from error

    @staticmethod
    def _decode(value):
        return json.loads(value) if value is not None else None

    @classmethod
    def _automation(cls, row):
        if row is None:
            return None
        return {
            "id": row[0], "user_id": row[1], "name": row[2], "folder_id": row[3],
            "data": cls._decode(row[4]), "meta": cls._decode(row[5]),
            "is_active": bool(row[6]), "created_at": row[7], "updated_at": row[8],
            "last_run_at": row[9], "next_run_at": row[10], "last_run": None,
        }

    @staticmethod
    def _select():
        return (
            "SELECT id,user_id,name,folder_id,data,meta,is_active,created_at,updated_at,"
            "last_run_at,next_run_at FROM openatelier_automation"
        )

    def get(self, automation_id):
        with self._connection() as db:
            row = db.execute(self._select() + " WHERE id=?", (automation_id,)).fetchone()
        return self._automation(row)

    def list(self, user_id, query=None, status=None, folder_id=None, page=1, limit=60):
        clauses, values = ["user_id=?"], [user_id]
        if query:
            clauses.append("LOWER(name) LIKE ?")
            values.append("%" + query.lower() + "%")
        if status == "active":
            clauses.append("is_active=1")
        elif status == "inactive":
            clauses.append("is_active=0")
        if folder_id is not None:
            clauses.append("folder_id=?")
            values.append(folder_id)
        where = " WHERE " + " AND ".join(clauses)
        with self._connection() as db:
            total = db.execute("SELECT count(*) FROM openatelier_automation" + where, values).fetchone()[0]
            rows = db.execute(
                self._select() + where + " ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
                (*values, limit, max(page - 1, 0) * limit),
            ).fetchall()
        return [self._automation(row) for row in rows], total

    def create(self, user_id, name, folder_id, data, meta, is_active, next_run_at):
        automation_id, now = str(uuid.uuid4()), time.time_ns()
        with self._connection() as db:
            db.execute(
                "INSERT INTO openatelier_automation "
                "(id,user_id,name,folder_id,data,meta,is_active,created_at,updated_at,last_run_at,next_run_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,NULL,?)",
                (automation_id, user_id, name, folder_id, data, meta, int(is_active), now, now, next_run_at),
            )
        return self.get(automation_id)

    def update(self, automation_id, user_id, name, folder_id, data, meta, is_active, next_run_at):
        now = time.time_ns()
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE openatelier_automation SET name=?,folder_id=?,data=?,meta=?,is_active=?,"
                "updated_at=?,next_run_at=? WHERE id=? AND user_id=?",
                (name, folder_id, data, meta, int(is_active), now, next_run_at, automation_id, user_id),
            )
        return self.get(automation_id) if cursor.rowcount else None

    def toggle(self, automation_id, user_id, next_run_at):
        now = time.time_ns()
        with self._connection() as db:
            row = db.execute(
                "SELECT is_active FROM openatelier_automation WHERE id=? AND user_id=?",
                (automation_id, user_id),
            ).fetchone()
            if row is None:
                return None
            active = not bool(row[0])
            db.execute(
                "UPDATE openatelier_automation SET is_active=?,updated_at=?,next_run_at=? WHERE id=?",
                (int(active), now, next_run_at if active else None, automation_id),
            )
        return self.get(automation_id)

    def runs(self, automation_id, skip=0, limit=50):
        with self._connection() as db:
            rows = db.execute(
                "SELECT id,automation_id,status,chat_id,error,created_at "
                "FROM openatelier_automation_run WHERE automation_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (automation_id, limit, skip),
            ).fetchall()
        return [
            {"id": row[0], "automation_id": row[1], "status": row[2], "chat_id": row[3],
             "error": row[4], "created_at": row[5]}
            for row in rows
        ]

    def mark_started(self, automation_id, user_id):
        now = time.time_ns()
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE openatelier_automation SET last_run_at=? WHERE id=? AND user_id=?",
                (now, automation_id, user_id),
            )
        return self.get(automation_id) if cursor.rowcount else None

    def record_run(self, automation_id, status, chat_id=None, error=None):
        if status not in ("success", "failed"):
            raise ValueError("unsupported automation run status")
        run_id, now = str(uuid.uuid4()), time.time_ns()
        with self._connection() as db:
            exists = db.execute(
                "SELECT 1 FROM openatelier_automation WHERE id=?", (automation_id,)
            ).fetchone()
            if exists is None:
                return None
            db.execute(
                "INSERT INTO openatelier_automation_run "
                "(id,automation_id,status,chat_id,error,created_at) VALUES(?,?,?,?,?,?)",
                (run_id, automation_id, status, chat_id, error, now),
            )
        return {
            "id": run_id,
            "automation_id": automation_id,
            "status": status,
            "chat_id": chat_id,
            "error": error,
            "created_at": now,
        }

    def delete(self, automation_id, user_id):
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM openatelier_automation WHERE id=? AND user_id=?",
                (automation_id, user_id),
            )
            if cursor.rowcount:
                db.execute("DELETE FROM openatelier_automation_run WHERE automation_id=?", (automation_id,))
        return bool(cursor.rowcount)
