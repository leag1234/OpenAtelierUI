# SPDX-License-Identifier: BSD-3-Clause
"""Opt-in reader for a bounded imported Tool/Model history sidecar.

Snapshots are inert JSON. This module never imports or executes their content.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import unquote

PAGE_SIZE = 20
MAX_DATASET_BYTES = 8 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_REVISIONS = 10_000
MAX_RESPONSE_BYTES = 1024 * 1024


class HistoryUnavailable(Exception):
    """Authorized history is not safely available."""


class HistoryNotFound(Exception):
    """Resource/version is absent from the imported history dataset."""


def _connect(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    uri = path.as_uri() + "?mode=ro" + ("&immutable=1" if immutable else "")
    connection = sqlite3.connect(uri, uri=True, timeout=1)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    return connection


def _regular_database(path: Path, maximum: int, *, reject_sidecars: bool = True) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise HistoryUnavailable("Imported history is unavailable")
    if reject_sidecars and any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise HistoryUnavailable("Imported history is unavailable")


def _row_fingerprint(kind: str, row: sqlite3.Row | dict) -> str:
    columns = {
        "tool": ("id", "user_id", "name", "content", "specs", "meta", "valves", "updated_at", "created_at"),
        "model": ("id", "user_id", "base_model_id", "name", "params", "meta", "is_active", "updated_at", "created_at"),
    }[kind]
    material = {key: row[key] for key in columns}
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def _target_row(target_db: Path, kind: str, resource_id: str):
    connection = _connect(target_db, immutable=False)
    try:
        row = connection.execute(f'SELECT * FROM "{kind}" WHERE id=?', (resource_id,)).fetchone()
        if row is None:
            raise HistoryUnavailable("Imported history is stale")
        return dict(row)
    except sqlite3.Error as error:
        raise HistoryUnavailable("Imported history is unavailable") from error
    finally:
        connection.close()


def write_history_sidecar(source_db: str | Path, target_db: str | Path, output: str | Path) -> dict:
    """Create a fresh history-only SQLite file from an already validated source."""
    source_path, target_path, output_path = Path(source_db), Path(target_db), Path(output)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("history sidecar destination already exists")
    source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True, timeout=1)
    target.row_factory = sqlite3.Row
    output_path.parent.mkdir(parents=True, exist_ok=True)
    destination = sqlite3.connect(output_path)
    try:
        destination.executescript("""
          PRAGMA journal_mode=DELETE;
          PRAGMA busy_timeout=1000;
          CREATE TABLE history_manifest(kind TEXT NOT NULL, resource_id TEXT NOT NULL, owner_id TEXT NOT NULL, current_version_id TEXT NOT NULL, fingerprint TEXT NOT NULL, row_count INTEGER NOT NULL, PRIMARY KEY(kind, resource_id));
          CREATE TABLE tool_history(id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, parent_id TEXT, snapshot TEXT NOT NULL, user_id TEXT NOT NULL, commit_message TEXT, created_at INTEGER NOT NULL);
          CREATE INDEX tool_history_page ON tool_history(tool_id, created_at DESC, id DESC);
          CREATE TABLE model_history(id TEXT PRIMARY KEY, model_id TEXT NOT NULL, parent_id TEXT, snapshot TEXT NOT NULL, user_id TEXT NOT NULL, commit_message TEXT, created_at INTEGER NOT NULL);
          CREATE INDEX model_history_page ON model_history(model_id, created_at DESC, id DESC);
        """)
        total = 0
        source_history_bytes = 0
        for kind, owner_column in (("tool", "tool_id"), ("model", "model_id")):
            history_table = kind + "_history"
            if history_table not in {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                continue
            rows = [dict(row) for row in source.execute(f'SELECT id,{owner_column},parent_id,snapshot,user_id,commit_message,created_at FROM "{history_table}" ORDER BY id')]
            if total + len(rows) > MAX_REVISIONS:
                raise ValueError("history revision limit exceeded")
            for row in rows:
                encoded = row["snapshot"]
                if not isinstance(encoded, str) or len(encoded.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
                    raise ValueError("history snapshot limit exceeded")
                source_history_bytes += len(encoded.encode("utf-8"))
                if source_history_bytes > MAX_DATASET_BYTES:
                    raise ValueError("history dataset limit exceeded")
                parsed = json.loads(encoded)
                if not isinstance(parsed, dict):
                    raise ValueError("history snapshot is not a JSON object")
                destination.execute(f'INSERT INTO "{history_table}" VALUES (?,?,?,?,?,?,?)', tuple(row[key] for key in ("id", owner_column, "parent_id", "snapshot", "user_id", "commit_message", "created_at")))
            counts = {}
            for row in target.execute(f'SELECT * FROM "{kind}" ORDER BY id'):
                resource_id = row["id"]
                resource_history = [item for item in rows if item[owner_column] == resource_id]
                if not resource_history:
                    continue
                version_id = source.execute(f'SELECT version_id FROM "{kind}" WHERE id=?', (resource_id,)).fetchone()[0]
                if not isinstance(version_id, str) or not version_id:
                    raise ValueError("history resource has no current version id")
                if not any(item["id"] == version_id and item[owner_column] == resource_id for item in resource_history):
                    raise ValueError("current version link is invalid")
                destination.execute("INSERT INTO history_manifest VALUES (?,?,?,?,?,?)", (kind, resource_id, row["user_id"], version_id, _row_fingerprint(kind, row), len(resource_history)))
                counts[(kind, resource_id)] = len(resource_history)
                total += len(resource_history)
            del counts
        destination.commit()
        destination.execute("PRAGMA optimize")
        destination.close()
        destination = None
        if output_path.stat().st_size > MAX_DATASET_BYTES:
            raise ValueError("history dataset limit exceeded")
        return {"path": output_path.name, "bytes": output_path.stat().st_size, "rows": total, "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}
    finally:
        source.close()
        target.close()
        if destination is not None:
            destination.close()


def _open_verified(data_dir: str | Path, database_url: str, kind: str, resource_id: str, owner_id: str):
    connection = None
    try:
        if os.environ.get("OPEN_WEBUI_IMPORTED_HISTORY_READS") != "1":
            raise HistoryUnavailable("Imported history is unavailable")
        if not database_url.startswith("sqlite:///"):
            raise HistoryUnavailable("Imported history is unavailable")
        data_root = Path(data_dir)
        app_db = data_root / "webui.db"
        configured_db = unquote(database_url[len("sqlite:///"):].split("?", 1)[0])
        if not Path(configured_db).is_absolute() or Path(configured_db).resolve() != app_db.resolve():
            raise HistoryUnavailable("Imported history is unavailable")
        sidecar = data_root / "imported-history.sqlite"
        _regular_database(app_db, 64 * 1024 * 1024, reject_sidecars=False)
        _regular_database(sidecar, MAX_DATASET_BYTES)
        connection = _connect(sidecar, immutable=True)
        manifest = connection.execute("SELECT * FROM history_manifest WHERE kind=? AND resource_id=?", (kind, resource_id)).fetchone()
        if manifest is None:
            connection.close()
            connection = None
            raise HistoryNotFound()
        if manifest["owner_id"] != owner_id:
            connection.close()
            connection = None
            raise HistoryUnavailable("Imported history is stale")
        manifest_count = manifest["row_count"]
        if not isinstance(manifest_count, int) or isinstance(manifest_count, bool) or not 0 <= manifest_count <= MAX_REVISIONS:
            connection.close()
            connection = None
            raise HistoryUnavailable("Imported history is unavailable")
        active = _target_row(app_db, kind, resource_id)
        if active.get("user_id") != owner_id or _row_fingerprint(kind, active) != manifest["fingerprint"]:
            connection.close()
            connection = None
            raise HistoryUnavailable("Imported history is stale")
        count = connection.execute(f'SELECT COUNT(*) FROM "{kind}_history" WHERE {kind}_id=?', (resource_id,)).fetchone()[0]
        total = connection.execute(f'SELECT COUNT(*) FROM "{kind}_history"').fetchone()[0]
        if count != manifest["row_count"] or total > MAX_REVISIONS:
            connection.close()
            connection = None
            raise HistoryUnavailable("Imported history is unavailable")
        return connection, manifest
    except HistoryNotFound:
        if connection is not None:
            connection.close()
        raise
    except HistoryUnavailable:
        if connection is not None:
            connection.close()
        raise
    except (sqlite3.Error, OSError, ValueError, OverflowError, TypeError, RuntimeError) as error:
        if connection is not None:
            connection.close()
        raise HistoryUnavailable("Imported history is unavailable") from error


def _project(connection, kind: str, row, user_lookup):
    if (
        not isinstance(row["id"], str)
        or not isinstance(row[f"{kind}_id"], str)
        or not isinstance(row["user_id"], str)
        or (row["parent_id"] is not None and not isinstance(row["parent_id"], str))
        or (row["commit_message"] is not None and not isinstance(row["commit_message"], str))
        or not isinstance(row["created_at"], int)
        or isinstance(row["created_at"], bool)
    ):
        raise HistoryUnavailable("Imported history is unavailable")
    user = user_lookup(row["user_id"])
    return {
        "id": row["id"], f"{kind}_id": row[f"{kind}_id"], "parent_id": row["parent_id"],
        "user_id": row["user_id"], "commit_message": row["commit_message"], "created_at": row["created_at"],
        "user": {"name": user.name} if user else None,
    }


def list_history(data_dir: str | Path, database_url: str, kind: str, resource, page: int, user_lookup):
    connection, manifest = _open_verified(data_dir, database_url, kind, resource.id, resource.user_id)
    try:
        normalized_page = max(1, page)
        page_count = (manifest["row_count"] + PAGE_SIZE - 1) // PAGE_SIZE
        if normalized_page > page_count:
            return []
        offset = (normalized_page - 1) * PAGE_SIZE
        rows = connection.execute(f'SELECT * FROM "{kind}_history" WHERE {kind}_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (resource.id, PAGE_SIZE, offset)).fetchall()
        result = [_project(connection, kind, row, user_lookup) for row in rows]
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise HistoryUnavailable("Imported history response is unavailable")
        return result
    except sqlite3.Error as error:
        raise HistoryUnavailable("Imported history is unavailable") from error
    finally:
        connection.close()


def get_history(data_dir: str | Path, database_url: str, kind: str, resource, version_id: str, user_lookup):
    connection, _ = _open_verified(data_dir, database_url, kind, resource.id, resource.user_id)
    try:
        row = connection.execute(f'SELECT * FROM "{kind}_history" WHERE {kind}_id=? AND id=?', (resource.id, version_id)).fetchone()
        if row is None:
            raise HistoryNotFound()
        snapshot_text = row["snapshot"]
        if not isinstance(snapshot_text, str) or len(snapshot_text.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise HistoryUnavailable("Imported history is unavailable")
        result = _project(connection, kind, row, user_lookup)
        snapshot = json.loads(snapshot_text)
        if not isinstance(snapshot, dict):
            raise HistoryUnavailable("Imported history is unavailable")
        result["snapshot"] = snapshot
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise HistoryUnavailable("Imported history response is unavailable")
        return result
    except (sqlite3.Error, ValueError) as error:
        if isinstance(error, HistoryUnavailable):
            raise
        raise HistoryUnavailable("Imported history is unavailable") from error
    finally:
        connection.close()
