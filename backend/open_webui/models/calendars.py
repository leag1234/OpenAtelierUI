# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""SQLite-only calendar store for the measured compatibility contract."""

import json
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


class CalendarsUnavailable(RuntimeError):
    pass


def _sqlite_path(database_url):
    if not isinstance(database_url, str) or not database_url.startswith("sqlite:///"):
        raise CalendarsUnavailable("Calendars require file-backed SQLite")
    raw = database_url[len("sqlite:///"):]
    if not raw or raw in (":memory:", "/:memory:") or "?" in raw or "#" in raw:
        raise CalendarsUnavailable("Calendars require file-backed SQLite")
    path = Path(raw)
    try:
        info = path.lstat()
    except OSError as error:
        raise CalendarsUnavailable("Calendar database is unavailable") from error
    if not path.is_absolute() or not stat.S_ISREG(info.st_mode):
        raise CalendarsUnavailable("Calendar database is unavailable")
    for parent in path.parents:
        try:
            parent_info = parent.lstat()
        except OSError as error:
            raise CalendarsUnavailable("Calendar database is unavailable") from error
        if not stat.S_ISDIR(parent_info.st_mode):
            raise CalendarsUnavailable("Calendar database is unavailable")
    return path


class Calendars:
    def __init__(self, database_url):
        self.path = _sqlite_path(database_url)
        self._prepare()

    def _connect(self):
        uri = "file:" + quote(str(self.path), safe="/") + "?mode=rw"
        try:
            return sqlite3.connect(uri, uri=True, timeout=1.0)
        except sqlite3.Error as error:
            raise CalendarsUnavailable("Calendar database is unavailable") from error

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
                    "CREATE TABLE IF NOT EXISTS openatelier_calendar ("
                    "id TEXT PRIMARY KEY,user_id TEXT NOT NULL,name TEXT NOT NULL,color TEXT,"
                    "data TEXT,meta TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,"
                    "is_default INTEGER NOT NULL DEFAULT 0,is_system INTEGER NOT NULL DEFAULT 0)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_calendar_owner_time "
                    "ON openatelier_calendar(user_id,created_at,id)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS openatelier_calendar_grant ("
                    "id TEXT PRIMARY KEY,calendar_id TEXT NOT NULL,principal_type TEXT NOT NULL,"
                    "principal_id TEXT NOT NULL,permission TEXT NOT NULL,created_at INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS openatelier_calendar_grant_identity "
                    "ON openatelier_calendar_grant(calendar_id,principal_type,principal_id,permission)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS openatelier_calendar_event ("
                    "id TEXT PRIMARY KEY,calendar_id TEXT NOT NULL,user_id TEXT NOT NULL,title TEXT NOT NULL,"
                    "description TEXT,start_at INTEGER NOT NULL,end_at INTEGER,all_day INTEGER NOT NULL DEFAULT 0,"
                    "location TEXT,color TEXT,rrule TEXT,data TEXT,meta TEXT,is_cancelled INTEGER NOT NULL DEFAULT 0,"
                    "created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS openatelier_calendar_event_range "
                    "ON openatelier_calendar_event(calendar_id,start_at,id)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS openatelier_calendar_attendee ("
                    "id TEXT PRIMARY KEY,event_id TEXT NOT NULL,user_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',"
                    "meta TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS openatelier_calendar_attendee_identity "
                    "ON openatelier_calendar_attendee(event_id,user_id)"
                )
                expected = {
                    "openatelier_calendar": (
                        ("id", "TEXT"), ("user_id", "TEXT"), ("name", "TEXT"), ("color", "TEXT"),
                        ("data", "TEXT"), ("meta", "TEXT"), ("created_at", "INTEGER"),
                        ("updated_at", "INTEGER"), ("is_default", "INTEGER"), ("is_system", "INTEGER"),
                    ),
                    "openatelier_calendar_grant": (
                        ("id", "TEXT"), ("calendar_id", "TEXT"), ("principal_type", "TEXT"),
                        ("principal_id", "TEXT"), ("permission", "TEXT"), ("created_at", "INTEGER"),
                    ),
                    "openatelier_calendar_event": (
                        ("id", "TEXT"), ("calendar_id", "TEXT"), ("user_id", "TEXT"), ("title", "TEXT"),
                        ("description", "TEXT"), ("start_at", "INTEGER"), ("end_at", "INTEGER"),
                        ("all_day", "INTEGER"), ("location", "TEXT"), ("color", "TEXT"), ("rrule", "TEXT"),
                        ("data", "TEXT"), ("meta", "TEXT"), ("is_cancelled", "INTEGER"),
                        ("created_at", "INTEGER"), ("updated_at", "INTEGER"),
                    ),
                    "openatelier_calendar_attendee": (
                        ("id", "TEXT"), ("event_id", "TEXT"), ("user_id", "TEXT"), ("status", "TEXT"),
                        ("meta", "TEXT"), ("created_at", "INTEGER"), ("updated_at", "INTEGER"),
                    ),
                }
                for table, columns in expected.items():
                    actual = tuple((row[1], row[2].upper()) for row in db.execute(f"PRAGMA table_info({table})"))
                    if actual != columns:
                        raise CalendarsUnavailable("Calendar table schema is unsupported")
        except sqlite3.Error as error:
            raise CalendarsUnavailable("Calendar database is unavailable") from error

    @staticmethod
    def _decode(value):
        return json.loads(value) if value is not None else None

    def _grants(self, db, calendar_id):
        return [
            {
                "id": row[0], "resource_type": "calendar", "resource_id": row[1],
                "principal_type": row[2], "principal_id": row[3],
                "permission": row[4], "created_at": row[5],
            }
            for row in db.execute(
                "SELECT id,calendar_id,principal_type,principal_id,permission,created_at "
                "FROM openatelier_calendar_grant WHERE calendar_id=? ORDER BY created_at,rowid",
                (calendar_id,),
            )
        ]

    def _calendar(self, db, row):
        if row is None:
            return None
        return {
            "id": row[0], "user_id": row[1], "name": row[2], "color": row[3],
            "data": self._decode(row[4]), "meta": self._decode(row[5]),
            "created_at": row[6], "updated_at": row[7],
            "is_default": bool(row[8]), "is_system": bool(row[9]),
            "access_grants": self._grants(db, row[0]),
        }

    def _calendar_row(self, db, calendar_id):
        return db.execute(
            "SELECT id,user_id,name,color,data,meta,created_at,updated_at,is_default,is_system "
            "FROM openatelier_calendar WHERE id=?", (calendar_id,),
        ).fetchone()

    def ensure_personal(self, user_id):
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM openatelier_calendar WHERE user_id=? AND is_default=1 ORDER BY created_at,id LIMIT 1",
                (user_id,),
            ).fetchone()
            if row is None:
                now = time.time_ns()
                calendar_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO openatelier_calendar "
                    "(id,user_id,name,color,data,meta,created_at,updated_at,is_default,is_system) "
                    "VALUES(?,?,?,?,?,?,?,?,1,0)",
                    (calendar_id, user_id, "Personal", "#3b82f6", None, None, now, now),
                )

    def ensure_system(self, user_id):
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM openatelier_calendar WHERE id='__scheduled_tasks__'"
            ).fetchone()
            if row is None:
                now = time.time_ns()
                db.execute(
                    "INSERT INTO openatelier_calendar "
                    "(id,user_id,name,color,data,meta,created_at,updated_at,is_default,is_system) "
                    "VALUES('__scheduled_tasks__',?,'Scheduled Tasks','#8b5cf6',NULL,NULL,?,?,0,1)",
                    (user_id, now, now),
                )

    @staticmethod
    def _principal_pairs(user_id, group_ids):
        return {("user", user_id), *(("group", value) for value in group_ids)}

    @classmethod
    def can_read(cls, calendar, user_id, group_ids, admin=False):
        if admin or calendar["user_id"] == user_id:
            return True
        principals = cls._principal_pairs(user_id, group_ids)
        return any(
            (grant["principal_type"], grant["principal_id"]) in principals
            and grant["permission"] == "read"
            for grant in calendar["access_grants"]
        )

    @classmethod
    def can_write(cls, calendar, user_id, group_ids, admin=False):
        if admin or calendar["user_id"] == user_id:
            return True
        principals = cls._principal_pairs(user_id, group_ids)
        return any(
            (grant["principal_type"], grant["principal_id"]) in principals
            and grant["permission"] == "write"
            for grant in calendar["access_grants"]
        )

    def get(self, calendar_id):
        with self._connection() as db:
            return self._calendar(db, self._calendar_row(db, calendar_id))

    def list_readable(self, user_id, group_ids):
        principals = self._principal_pairs(user_id, group_ids)
        with self._connection() as db:
            rows = db.execute(
                "SELECT id,user_id,name,color,data,meta,created_at,updated_at,is_default,is_system "
                "FROM openatelier_calendar ORDER BY created_at,id"
            ).fetchall()
            calendars = [self._calendar(db, row) for row in rows]
        return [
            value for value in calendars
            if value["user_id"] == user_id or any(
                (grant["principal_type"], grant["principal_id"]) in principals
                and grant["permission"] == "read"
                for grant in value["access_grants"]
            )
        ]

    @staticmethod
    def _replace_grants(db, calendar_id, grants):
        db.execute("DELETE FROM openatelier_calendar_grant WHERE calendar_id=?", (calendar_id,))
        created_at = int(time.time())
        for grant in grants:
            db.execute(
                "INSERT INTO openatelier_calendar_grant "
                "(id,calendar_id,principal_type,principal_id,permission,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), calendar_id, grant["principal_type"], grant["principal_id"], grant["permission"], created_at),
            )

    def create(self, user_id, name, color, data, meta, grants):
        calendar_id, now = str(uuid.uuid4()), time.time_ns()
        with self._connection() as db:
            db.execute(
                "INSERT INTO openatelier_calendar "
                "(id,user_id,name,color,data,meta,created_at,updated_at,is_default,is_system) "
                "VALUES(?,?,?,?,?,?,?,?,0,0)",
                (calendar_id, user_id, name, color, data, meta, now, now),
            )
            self._replace_grants(db, calendar_id, grants)
            return self._calendar(db, self._calendar_row(db, calendar_id))

    def update(self, calendar_id, values, grants=None):
        allowed = ("name", "color", "data", "meta")
        assignments, parameters = [], []
        for field in allowed:
            if field in values:
                assignments.append(field + "=?")
                parameters.append(values[field])
        with self._connection() as db:
            if assignments:
                assignments.append("updated_at=?")
                parameters.append(time.time_ns())
                db.execute(
                    "UPDATE openatelier_calendar SET " + ",".join(assignments) + " WHERE id=?",
                    (*parameters, calendar_id),
                )
            if grants is not None:
                self._replace_grants(db, calendar_id, grants)
            return self._calendar(db, self._calendar_row(db, calendar_id))

    def set_default(self, calendar_id, user_id):
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM openatelier_calendar WHERE id=? AND user_id=?", (calendar_id, user_id),
            ).fetchone()
            if row is None:
                return None
            now = time.time_ns()
            db.execute("UPDATE openatelier_calendar SET is_default=0 WHERE user_id=?", (user_id,))
            db.execute(
                "UPDATE openatelier_calendar SET is_default=1,updated_at=? WHERE id=?", (now, calendar_id),
            )
            return self._calendar(db, self._calendar_row(db, calendar_id))

    def delete(self, calendar_id):
        with self._connection() as db:
            event_ids = [row[0] for row in db.execute(
                "SELECT id FROM openatelier_calendar_event WHERE calendar_id=?", (calendar_id,),
            )]
            if event_ids:
                slots = ",".join("?" for _ in event_ids)
                db.execute(f"DELETE FROM openatelier_calendar_attendee WHERE event_id IN ({slots})", event_ids)
            db.execute("DELETE FROM openatelier_calendar_event WHERE calendar_id=?", (calendar_id,))
            db.execute("DELETE FROM openatelier_calendar_grant WHERE calendar_id=?", (calendar_id,))
            return db.execute("DELETE FROM openatelier_calendar WHERE id=?", (calendar_id,)).rowcount == 1

    def _attendees(self, db, event_id):
        return [
            {
                "id": row[0], "event_id": row[1], "user_id": row[2], "status": row[3],
                "meta": self._decode(row[4]), "created_at": row[5], "updated_at": row[6],
            }
            for row in db.execute(
                "SELECT id,event_id,user_id,status,meta,created_at,updated_at "
                "FROM openatelier_calendar_attendee WHERE event_id=? ORDER BY created_at,id",
                (event_id,),
            )
        ]

    def _event(self, db, row):
        if row is None:
            return None
        return {
            "id": row[0], "calendar_id": row[1], "user_id": row[2], "title": row[3],
            "description": row[4], "start_at": row[5], "end_at": row[6],
            "all_day": bool(row[7]), "location": row[8], "color": row[9], "rrule": row[10],
            "data": self._decode(row[11]), "meta": self._decode(row[12]),
            "is_cancelled": bool(row[13]), "created_at": row[14], "updated_at": row[15],
            "attendees": self._attendees(db, row[0]),
        }

    def _event_row(self, db, event_id):
        return db.execute(
            "SELECT id,calendar_id,user_id,title,description,start_at,end_at,all_day,location,color,rrule,"
            "data,meta,is_cancelled,created_at,updated_at FROM openatelier_calendar_event WHERE id=?",
            (event_id,),
        ).fetchone()

    def get_event(self, event_id):
        with self._connection() as db:
            return self._event(db, self._event_row(db, event_id))

    @staticmethod
    def _replace_attendees(db, event_id, attendees):
        db.execute("DELETE FROM openatelier_calendar_attendee WHERE event_id=?", (event_id,))
        for attendee in attendees:
            now = time.time_ns()
            db.execute(
                "INSERT INTO openatelier_calendar_attendee "
                "(id,event_id,user_id,status,meta,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), event_id, attendee["user_id"], attendee.get("status", "pending"), attendee.get("meta"), now, now),
            )

    def create_event(self, user_id, values, attendees):
        event_id, now = str(uuid.uuid4()), time.time_ns()
        with self._connection() as db:
            db.execute(
                "INSERT INTO openatelier_calendar_event "
                "(id,calendar_id,user_id,title,description,start_at,end_at,all_day,location,color,rrule,data,meta,"
                "is_cancelled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (
                    event_id, values["calendar_id"], user_id, values["title"], values.get("description"),
                    values["start_at"], values.get("end_at"), int(values.get("all_day", False)),
                    values.get("location"), values.get("color"), values.get("rrule"),
                    values.get("data"), values.get("meta"), now, now,
                ),
            )
            self._replace_attendees(db, event_id, attendees)
            return self._event(db, self._event_row(db, event_id))

    def update_event(self, event_id, values, attendees=None):
        allowed = (
            "calendar_id", "title", "description", "start_at", "end_at", "all_day", "location",
            "color", "rrule", "data", "meta", "is_cancelled",
        )
        assignments, parameters = [], []
        for field in allowed:
            if field in values:
                assignments.append(field + "=?")
                value = values[field]
                parameters.append(int(value) if field in ("all_day", "is_cancelled") else value)
        with self._connection() as db:
            if assignments:
                assignments.append("updated_at=?")
                parameters.append(time.time_ns())
                db.execute(
                    "UPDATE openatelier_calendar_event SET " + ",".join(assignments) + " WHERE id=?",
                    (*parameters, event_id),
                )
            if attendees is not None:
                self._replace_attendees(db, event_id, attendees)
            return self._event(db, self._event_row(db, event_id))

    def list_events(self, calendar_ids, start_at=None, end_at=None, query=None, skip=0, limit=None):
        if not calendar_ids:
            return [], 0
        slots = ",".join("?" for _ in calendar_ids)
        where, parameters = [f"calendar_id IN ({slots})"], list(calendar_ids)
        if start_at is not None:
            where.append("start_at>=?")
            parameters.append(start_at)
        if end_at is not None:
            where.append("start_at<=?")
            parameters.append(end_at)
        if query is not None:
            where.append("(title LIKE ? OR coalesce(description,'') LIKE ? OR coalesce(location,'') LIKE ?)")
            pattern = "%" + query + "%"
            parameters.extend((pattern, pattern, pattern))
        sql = " WHERE " + " AND ".join(where)
        with self._connection() as db:
            total = db.execute("SELECT count(*) FROM openatelier_calendar_event" + sql, parameters).fetchone()[0]
            suffix = " ORDER BY start_at,id"
            result_parameters = list(parameters)
            if limit is not None:
                suffix += " LIMIT ? OFFSET ?"
                result_parameters.extend((limit, skip))
            rows = db.execute(
                "SELECT id,calendar_id,user_id,title,description,start_at,end_at,all_day,location,color,rrule,"
                "data,meta,is_cancelled,created_at,updated_at FROM openatelier_calendar_event" + sql + suffix,
                result_parameters,
            ).fetchall()
            return [self._event(db, row) for row in rows], total

    def rsvp(self, event_id, user_id, status):
        with self._connection() as db:
            row = db.execute(
                "SELECT id FROM openatelier_calendar_attendee WHERE event_id=? AND user_id=?",
                (event_id, user_id),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "UPDATE openatelier_calendar_attendee SET status=?,updated_at=? WHERE id=?",
                (status, time.time_ns(), row[0]),
            )
            return True

    def delete_event(self, event_id):
        with self._connection() as db:
            db.execute("DELETE FROM openatelier_calendar_attendee WHERE event_id=?", (event_id,))
            return db.execute("DELETE FROM openatelier_calendar_event WHERE id=?", (event_id,)).rowcount == 1
