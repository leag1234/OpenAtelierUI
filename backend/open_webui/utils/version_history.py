# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded local version history for editable workspace resources.

Snapshots are inert JSON. Reading and comparing history never imports or executes
stored function or tool source. Routers remain responsible for validating a
snapshot before selecting it as the active version.
"""

from __future__ import annotations

import difflib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


MAX_REVISIONS = 10_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_SNAPSHOT_DATASET_BYTES = 48 * 1024 * 1024
PAGE_SIZE = 50
_KINDS = {"function", "model", "skill", "tool"}


class VersionHistoryNotFound(Exception):
    pass


class VersionHistoryUnavailable(Exception):
    pass


def _path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "version-history.sqlite"


def _connect(data_dir: str | Path) -> sqlite3.Connection:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise VersionHistoryUnavailable("Version history is unavailable")
    if path.exists() and path.stat().st_size > MAX_DATABASE_BYTES:
        raise VersionHistoryUnavailable("Version history limit exceeded")
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS version_history (
            kind TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            id TEXT NOT NULL,
            parent_id TEXT,
            snapshot TEXT NOT NULL,
            user_id TEXT NOT NULL,
            commit_message TEXT,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(kind, resource_id, id)
        );
        CREATE INDEX IF NOT EXISTS version_history_page
            ON version_history(kind, resource_id, created_at DESC, id DESC);
        CREATE TABLE IF NOT EXISTS version_pointer (
            kind TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            current_version_id TEXT NOT NULL,
            PRIMARY KEY(kind, resource_id)
        );
        """
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return connection


def _validate(kind: str, resource_id: str, owner_id: str, snapshot: dict) -> str:
    if kind not in _KINDS:
        raise VersionHistoryUnavailable("Version history is unavailable")
    if not all(isinstance(value, str) and value for value in (resource_id, owner_id)):
        raise VersionHistoryUnavailable("Version history is unavailable")
    if not isinstance(snapshot, dict):
        raise VersionHistoryUnavailable("Version snapshot is invalid")
    try:
        encoded = json.dumps(
            snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        encoded.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise VersionHistoryUnavailable("Version snapshot is invalid") from error
    if len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise VersionHistoryUnavailable("Version snapshot limit exceeded")
    return encoded


def _pointer(connection: sqlite3.Connection, kind: str, resource_id: str):
    return connection.execute(
        "SELECT * FROM version_pointer WHERE kind=? AND resource_id=?",
        (kind, resource_id),
    ).fetchone()


def append_version(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    snapshot: dict,
    user_id: str,
    commit_message: str | None = None,
) -> str:
    encoded = _validate(kind, resource_id, owner_id, snapshot)
    if not isinstance(user_id, str) or not user_id:
        raise VersionHistoryUnavailable("Version history is unavailable")
    if commit_message is not None and not isinstance(commit_message, str):
        raise VersionHistoryUnavailable("Version history is unavailable")
    if commit_message is not None and len(commit_message.encode("utf-8")) > 4096:
        raise VersionHistoryUnavailable("Version commit message limit exceeded")
    version_id = str(uuid.uuid4())
    connection = _connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT COUNT(*) FROM version_history").fetchone()[0] >= MAX_REVISIONS:
            raise VersionHistoryUnavailable("Version history limit exceeded")
        snapshot_bytes = connection.execute(
            "SELECT COALESCE(SUM(length(CAST(snapshot AS BLOB))),0) FROM version_history"
        ).fetchone()[0]
        if snapshot_bytes + len(encoded.encode("utf-8")) > MAX_SNAPSHOT_DATASET_BYTES:
            raise VersionHistoryUnavailable("Version history limit exceeded")
        pointer = _pointer(connection, kind, resource_id)
        parent_id = pointer["current_version_id"] if pointer else None
        connection.execute(
            "INSERT INTO version_history VALUES (?,?,?,?,?,?,?,?)",
            (
                kind,
                resource_id,
                version_id,
                parent_id,
                encoded,
                user_id,
                commit_message,
                int(time.time()),
            ),
        )
        connection.execute(
            """INSERT INTO version_pointer VALUES (?,?,?,?)
               ON CONFLICT(kind,resource_id) DO UPDATE SET
               owner_id=excluded.owner_id,
               current_version_id=excluded.current_version_id""",
            (kind, resource_id, owner_id, version_id),
        )
        connection.commit()
    except VersionHistoryUnavailable:
        connection.rollback()
        raise
    except (sqlite3.Error, OSError) as error:
        connection.rollback()
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()
    if _path(data_dir).stat().st_size > MAX_DATABASE_BYTES:
        raise VersionHistoryUnavailable("Version history limit exceeded")
    return version_id


def ensure_root(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    snapshot: dict,
    user_id: str,
) -> str:
    try:
        return current_version_id(data_dir, kind, resource_id, owner_id)
    except VersionHistoryNotFound:
        return append_version(data_dir, kind, resource_id, owner_id, snapshot, user_id)


def current_version_id(
    data_dir: str | Path, kind: str, resource_id: str, owner_id: str
) -> str:
    connection = _connect(data_dir)
    try:
        pointer = _pointer(connection, kind, resource_id)
        if pointer is None:
            raise VersionHistoryNotFound()
        if pointer["owner_id"] != owner_id:
            raise VersionHistoryUnavailable("Version history owner mismatch")
        return pointer["current_version_id"]
    except sqlite3.Error as error:
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()


def _project(row: sqlite3.Row, kind: str, user_lookup) -> dict:
    user = user_lookup(row["user_id"])
    return {
        "id": row["id"],
        f"{kind}_id": row["resource_id"],
        "parent_id": row["parent_id"],
        "user_id": row["user_id"],
        "commit_message": row["commit_message"],
        "created_at": row["created_at"],
        "user": {"name": user.name} if user else None,
    }


def list_versions(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    page: int,
    user_lookup,
) -> list[dict]:
    connection = _connect(data_dir)
    try:
        pointer = _pointer(connection, kind, resource_id)
        if pointer is None:
            raise VersionHistoryNotFound()
        if pointer["owner_id"] != owner_id:
            raise VersionHistoryUnavailable("Version history owner mismatch")
        offset = (max(page, 1) - 1) * PAGE_SIZE
        rows = connection.execute(
            """SELECT rowid,* FROM version_history
               WHERE kind=? AND resource_id=?
               ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?""",
            (kind, resource_id, PAGE_SIZE, offset),
        ).fetchall()
        return [_project(row, kind, user_lookup) for row in rows]
    except sqlite3.Error as error:
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()


def get_version(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    version_id: str,
    user_lookup,
) -> dict:
    connection = _connect(data_dir)
    try:
        pointer = _pointer(connection, kind, resource_id)
        if pointer is None:
            raise VersionHistoryNotFound()
        if pointer["owner_id"] != owner_id:
            raise VersionHistoryUnavailable("Version history owner mismatch")
        row = connection.execute(
            """SELECT * FROM version_history
               WHERE kind=? AND resource_id=? AND id=?""",
            (kind, resource_id, version_id),
        ).fetchone()
        if row is None:
            raise VersionHistoryNotFound()
        result = _project(row, kind, user_lookup)
        result["snapshot"] = json.loads(row["snapshot"])
        return result
    except (sqlite3.Error, json.JSONDecodeError) as error:
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()


def compare_versions(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    from_id: str,
    to_id: str,
    user_lookup,
) -> dict:
    before = get_version(
        data_dir, kind, resource_id, owner_id, from_id, user_lookup
    )["snapshot"]
    after = get_version(
        data_dir, kind, resource_id, owner_id, to_id, user_lookup
    )["snapshot"]
    before_content = before.get("content", "")
    after_content = after.get("content", "")
    if not isinstance(before_content, str) or not isinstance(after_content, str):
        raise VersionHistoryUnavailable("Version snapshot is invalid")
    content_diff = "\n".join(
        difflib.unified_diff(
            before_content.splitlines(),
            after_content.splitlines(),
            fromfile="selected.py",
            tofile="production.py",
            lineterm="",
        )
    )
    metadata = {}
    for key in ("name", "meta"):
        if before.get(key) != after.get(key):
            metadata[key] = {"before": before.get(key), "after": after.get(key)}
    return {
        "from_id": from_id,
        "to_id": to_id,
        "content_diff": content_diff,
        "line_endings_only": (
            before_content != after_content
            and before_content.replace("\r\n", "\n")
            == after_content.replace("\r\n", "\n")
        ),
        "metadata": metadata,
    }


def compare_version_files(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    from_id: str,
    to_id: str,
    path: str,
    user_lookup,
) -> dict:
    """Compare one inert text file held in two resource snapshots."""

    before = get_version(
        data_dir, kind, resource_id, owner_id, from_id, user_lookup
    )["snapshot"]
    after = get_version(
        data_dir, kind, resource_id, owner_id, to_id, user_lookup
    )["snapshot"]

    def content(snapshot: dict) -> str:
        files = snapshot.get("data", {}).get("files", [])
        for item in files:
            if isinstance(item, dict) and item.get("path") == path:
                value = item.get("content")
                if isinstance(value, str):
                    return value
                break
        raise VersionHistoryNotFound()

    before_content, after_content = content(before), content(after)
    return {
        "from_id": from_id,
        "to_id": to_id,
        "path": path,
        "content_diff": "\n".join(
            difflib.unified_diff(
                before_content.splitlines(),
                after_content.splitlines(),
                fromfile=path,
                tofile=path,
                lineterm="",
            )
        ),
        "line_endings_only": (
            before_content != after_content
            and before_content.replace("\r\n", "\n")
            == after_content.replace("\r\n", "\n")
        ),
    }


def select_version(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    version_id: str,
) -> None:
    connection = _connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        pointer = _pointer(connection, kind, resource_id)
        row = connection.execute(
            """SELECT 1 FROM version_history
               WHERE kind=? AND resource_id=? AND id=?""",
            (kind, resource_id, version_id),
        ).fetchone()
        if pointer is None or row is None:
            raise VersionHistoryNotFound()
        if pointer["owner_id"] != owner_id:
            raise VersionHistoryUnavailable("Version history owner mismatch")
        connection.execute(
            """UPDATE version_pointer SET current_version_id=?
               WHERE kind=? AND resource_id=?""",
            (version_id, kind, resource_id),
        )
        connection.commit()
    except VersionHistoryNotFound:
        connection.rollback()
        raise
    except sqlite3.Error as error:
        connection.rollback()
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()


def delete_version(
    data_dir: str | Path,
    kind: str,
    resource_id: str,
    owner_id: str,
    version_id: str,
) -> bool:
    connection = _connect(data_dir)
    try:
        connection.execute("BEGIN IMMEDIATE")
        pointer = _pointer(connection, kind, resource_id)
        row = connection.execute(
            """SELECT parent_id FROM version_history
               WHERE kind=? AND resource_id=? AND id=?""",
            (kind, resource_id, version_id),
        ).fetchone()
        if pointer is None or row is None:
            raise VersionHistoryNotFound()
        if pointer["owner_id"] != owner_id:
            raise VersionHistoryUnavailable("Version history owner mismatch")
        if pointer["current_version_id"] == version_id:
            raise VersionHistoryUnavailable("The active version cannot be deleted")
        connection.execute(
            """UPDATE version_history SET parent_id=?
               WHERE kind=? AND resource_id=? AND parent_id=?""",
            (row["parent_id"], kind, resource_id, version_id),
        )
        connection.execute(
            """DELETE FROM version_history
               WHERE kind=? AND resource_id=? AND id=?""",
            (kind, resource_id, version_id),
        )
        connection.commit()
        return True
    except (VersionHistoryNotFound, VersionHistoryUnavailable):
        connection.rollback()
        raise
    except sqlite3.Error as error:
        connection.rollback()
        raise VersionHistoryUnavailable("Version history is unavailable") from error
    finally:
        connection.close()
