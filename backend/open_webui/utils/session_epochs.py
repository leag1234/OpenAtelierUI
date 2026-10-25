# SPDX-License-Identifier: BSD-3-Clause
"""Inactive, copy-only SQLite account epoch experiment.

This module is not imported by the application. Callers must pass an existing
trusted SQLite path explicitly, and the copy-preparation operation is the only
operation that adds ``auth.session_stamp``.
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
import time
import uuid
from pathlib import Path


MAX_DATABASE_BYTES = 16 * 1024 * 1024
MAX_ACCOUNTS = 1000
MAX_EPOCH_CHARS = 256
MAX_EPOCH_BYTES = 512
DEFAULT_BUSY_TIMEOUT = 0.05


class SessionEpochError(Exception):
    """A named, fail-closed component or copy-preparation refusal."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _regular_db_path(path: str | Path, *, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise SessionEpochError("invalid_database_path", f"{label} must be an existing regular non-symlink database file")
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise SessionEpochError("invalid_database_path", f"{label} is unavailable") from error
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise SessionEpochError("invalid_database_path", f"{label} is unavailable") from error
    if size > MAX_DATABASE_BYTES:
        raise SessionEpochError("database_too_large", f"{label} exceeds the {MAX_DATABASE_BYTES}-byte experiment limit")
    return candidate


def _connect_rw(path: str | Path, *, busy_timeout: float, query_counter: dict | None = None) -> sqlite3.Connection:
    if not isinstance(busy_timeout, (int, float)) or not 0 <= busy_timeout <= 5:
        raise SessionEpochError("invalid_busy_timeout", "busy timeout must be between 0 and 5 seconds")
    candidate = _regular_db_path(path, label="database")
    try:
        connection = sqlite3.connect(
            candidate.as_uri() + "?mode=rw",
            uri=True,
            timeout=float(busy_timeout),
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(busy_timeout * 1000)}")
        if query_counter is not None:
            query_counter.setdefault("statements", 0)
            query_counter.setdefault("selects", 0)
            query_counter.setdefault("writes", 0)

            def count_statement(sql: str) -> None:
                query_counter["statements"] += 1
                command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
                if command == "SELECT":
                    query_counter["selects"] += 1
                elif command in {"INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "CREATE", "DROP"}:
                    query_counter["writes"] += 1

            connection.set_trace_callback(count_statement)
        return connection
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _database_error(error: sqlite3.Error) -> SessionEpochError:
    message = str(error).lower()
    if "locked" in message or "busy" in message:
        return SessionEpochError("database_busy", "database remained busy through the bounded lock timeout")
    if "malformed" in message or "not a database" in message or "disk image" in message:
        return SessionEpochError("database_corrupt", "SQLite could not validate the supplied database")
    return SessionEpochError("database_error", "SQLite could not complete the requested epoch operation")


def _require_schema(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute('PRAGMA table_info("auth")').fetchall()
    except sqlite3.Error as error:
        raise _database_error(error) from error
    columns = {row["name"] for row in rows}
    if not columns:
        raise SessionEpochError("auth_table_missing", "database has no auth table")
    if not {"id", "session_stamp"}.issubset(columns):
        raise SessionEpochError("session_stamp_schema_missing", "database lacks the prepared auth.session_stamp field")
    _require_single_id_primary_key(rows)


def _require_single_id_primary_key(rows) -> None:
    primary_key_columns = [(row[1], row[5]) for row in rows if row[5]]
    if primary_key_columns != [("id", 1)]:
        raise SessionEpochError(
            "auth_identity_schema_ambiguous",
            "auth.id must be the table's sole declared primary-key column",
        )


def _check_user_id(user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id or len(user_id) > 256:
        raise SessionEpochError("invalid_user_id", "user id must be a nonempty bounded string")


def _check_epoch(value, *, allow_null: bool = False) -> str | None:
    if value is None and allow_null:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_EPOCH_CHARS:
        raise SessionEpochError("invalid_epoch_state", "stored session stamp is null, malformed, or exceeds the bound")
    try:
        encoded_size = len(value.encode("utf-8", errors="strict"))
    except UnicodeEncodeError as error:
        raise SessionEpochError("invalid_epoch_state", "stored session stamp is not valid UTF-8 text") from error
    if encoded_size > MAX_EPOCH_BYTES:
        raise SessionEpochError("invalid_epoch_state", "stored session stamp is null, malformed, or exceeds the bound")
    return value


def _read_row(connection: sqlite3.Connection, user_id: str):
    row = connection.execute(
        'SELECT id, session_stamp FROM "auth" WHERE id = ?', (user_id,)
    ).fetchone()
    if row is None:
        raise SessionEpochError("user_not_found", "no existing auth row matches the requested user")
    return row


def get_epoch(database_path: str | Path, user_id: str, *, busy_timeout: float = DEFAULT_BUSY_TIMEOUT, query_counter: dict | None = None) -> str | None:
    """Read a known user's bounded epoch; a stored NULL remains distinguishable."""
    _check_user_id(user_id)
    connection = _connect_rw(database_path, busy_timeout=busy_timeout, query_counter=query_counter)
    try:
        _require_schema(connection)
        row = _read_row(connection, user_id)
        return _check_epoch(row["session_stamp"], allow_null=True)
    except SessionEpochError:
        raise
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        connection.close()


def issue_epoch(database_path: str | Path, user_id: str, *, busy_timeout: float = DEFAULT_BUSY_TIMEOUT, query_counter: dict | None = None) -> str:
    """Return the stable epoch, initializing only an existing NULL auth row."""
    _check_user_id(user_id)
    connection = _connect_rw(database_path, busy_timeout=busy_timeout, query_counter=query_counter)
    try:
        _require_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = _read_row(connection, user_id)
        current = _check_epoch(row["session_stamp"], allow_null=True)
        if current is None:
            current = uuid.uuid4().hex
            result = connection.execute(
                'UPDATE "auth" SET session_stamp = ? WHERE id = ? AND session_stamp IS NULL',
                (current, user_id),
            )
            if result.rowcount != 1:
                raise SessionEpochError("concurrent_update", "auth epoch changed during initialization")
        connection.commit()
        return current
    except SessionEpochError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.rollback()
        raise _database_error(error) from error
    finally:
        connection.close()


def validate_epoch(database_path: str | Path, user_id: str, expected_epoch: str, *, busy_timeout: float = DEFAULT_BUSY_TIMEOUT, query_counter: dict | None = None) -> bool:
    """Compare a future signature-validated claim to the trusted stored epoch."""
    _check_user_id(user_id)
    expected = _check_epoch(expected_epoch)
    connection = _connect_rw(database_path, busy_timeout=busy_timeout, query_counter=query_counter)
    try:
        _require_schema(connection)
        row = _read_row(connection, user_id)
        current = _check_epoch(row["session_stamp"], allow_null=True)
        return current is not None and current == expected
    except SessionEpochError:
        raise
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        connection.close()


def rotate_epoch(database_path: str | Path, user_id: str, *, busy_timeout: float = DEFAULT_BUSY_TIMEOUT, query_counter: dict | None = None) -> str:
    """Atomically replace only one existing auth row's session stamp."""
    _check_user_id(user_id)
    connection = _connect_rw(database_path, busy_timeout=busy_timeout, query_counter=query_counter)
    try:
        _require_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = _read_row(connection, user_id)
        previous = _check_epoch(row["session_stamp"], allow_null=True)
        replacement = uuid.uuid4().hex
        while replacement == previous:
            replacement = uuid.uuid4().hex
        result = connection.execute(
            'UPDATE "auth" SET session_stamp = ? WHERE id = ? AND session_stamp IS ?',
            (replacement, user_id, previous),
        )
        if result.rowcount != 1:
            raise SessionEpochError("concurrent_update", "auth epoch changed during rotation")
        connection.commit()
        return replacement
    except SessionEpochError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error as error:
        if connection.in_transaction:
            connection.rollback()
        raise _database_error(error) from error
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_integrity(path: Path) -> None:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise _database_error(error) from error
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise SessionEpochError("database_integrity_failed", "SQLite integrity check did not return ok")
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        connection.close()


def prepare_auth_session_stamp_copy(source_path: str | Path, target_path: str | Path) -> dict:
    """Copy a closed old-format fixture and add the candidate field only there."""
    source = Path(source_path)
    target = Path(target_path)
    if source.is_symlink() or not source.is_file():
        raise SessionEpochError("invalid_source_path", "source must be an existing regular non-symlink file")
    source = source.resolve(strict=True)
    try:
        source_size = source.stat().st_size
    except OSError as error:
        raise SessionEpochError("invalid_source_path", "source is unavailable") from error
    if source_size > MAX_DATABASE_BYTES:
        raise SessionEpochError("database_too_large", f"source exceeds the {MAX_DATABASE_BYTES}-byte experiment limit")
    if any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise SessionEpochError("ambiguous_sqlite_state", "source has SQLite sidecars; supply a closed checkpointed copy")
    if target.exists() or target.is_symlink():
        raise SessionEpochError("target_already_exists", "copy target must be fresh and is never overwritten")
    try:
        parent = target.parent.resolve(strict=True)
    except OSError as error:
        raise SessionEpochError("target_parent_missing", "target parent must already exist") from error
    target = parent / target.name
    if source == target:
        raise SessionEpochError("source_target_same", "source and target must be different files")
    source_hash = _sha256(source)
    _check_integrity(source)
    try:
        inspect = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            auth_rows = inspect.execute('PRAGMA table_info("auth")').fetchall()
            columns = {row[1] for row in auth_rows}
            if not columns:
                raise SessionEpochError("auth_table_missing", "source has no auth table")
            required = {"id", "email", "password", "active"}
            if not required.issubset(columns):
                raise SessionEpochError("auth_schema_mismatch", "source auth table lacks required old-format columns")
            _require_single_id_primary_key(auth_rows)
            if "session_stamp" in columns:
                raise SessionEpochError("session_stamp_already_present", "copy preparation accepts only the old-format auth schema")
            account_count = inspect.execute('SELECT COUNT(*) FROM "auth"').fetchone()[0]
            if type(account_count) is not int or account_count > MAX_ACCOUNTS:
                raise SessionEpochError("account_limit_exceeded", f"source exceeds the {MAX_ACCOUNTS}-account experiment limit")
        finally:
            inspect.close()
    except SessionEpochError:
        raise
    except sqlite3.Error as error:
        raise _database_error(error) from error

    attempt = parent / f".{target.name}.attempt-{uuid.uuid4().hex}"
    try:
        shutil.copyfile(source, attempt)
        if _sha256(attempt) != source_hash:
            raise SessionEpochError("copy_hash_mismatch", "copy bytes do not match the source hash")
        connection = _connect_rw(attempt, busy_timeout=DEFAULT_BUSY_TIMEOUT)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute('ALTER TABLE "auth" ADD COLUMN session_stamp TEXT')
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        _check_integrity(attempt)
        if _sha256(source) != source_hash:
            raise SessionEpochError("source_changed_during_copy", "source hash changed during copy preparation")
        # A hard link publishes atomically and fails if another writer created target.
        os.link(attempt, target)
        attempt.unlink()
        return {
            "status": "prepared_copy",
            "source_sha256": source_hash,
            "target_sha256": _sha256(target),
            "target_bytes": target.stat().st_size,
            "auth_account_count": account_count,
            "added_column": "auth.session_stamp TEXT",
            "source_unchanged": _sha256(source) == source_hash,
            "target_integrity": "ok",
        }
    except SessionEpochError:
        raise
    except FileExistsError as error:
        raise SessionEpochError("target_already_exists", "copy target appeared during preparation and was not overwritten") from error
    except OSError as error:
        retained = f"; retained attempt is {attempt}" if attempt.exists() else ""
        raise SessionEpochError("copy_preparation_failed", f"could not atomically publish the prepared copy{retained}") from error
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[index], 6)


def measure_component(database_path: str | Path, user_id: str, *, iterations: int = 100) -> dict:
    """Measure the explicit component on a disposable prepared synthetic copy."""
    if type(iterations) is not int or not 1 <= iterations <= MAX_ACCOUNTS:
        raise SessionEpochError("invalid_iteration_count", f"iterations must be between 1 and {MAX_ACCOUNTS}")
    path = _regular_db_path(database_path, label="prepared database")
    epoch = issue_epoch(path, user_id)
    issue_times = []
    issue_queries = {"statements": 0, "selects": 0, "writes": 0}
    validate_times = []
    validate_queries = {"statements": 0, "selects": 0, "writes": 0}
    rotate_times = []
    rotate_queries = {"statements": 0, "selects": 0, "writes": 0}
    for _ in range(iterations):
        counter = {"statements": 0, "selects": 0, "writes": 0}
        started = time.perf_counter()
        current = issue_epoch(path, user_id, query_counter=counter)
        issue_times.append((time.perf_counter() - started) * 1000)
        for key in issue_queries:
            issue_queries[key] += counter[key]
        if current != epoch:
            raise SessionEpochError("unstable_epoch", "issuing an existing epoch unexpectedly changed it")
        counter = {"statements": 0, "selects": 0, "writes": 0}
        started = time.perf_counter()
        valid = validate_epoch(path, user_id, epoch, query_counter=counter)
        validate_times.append((time.perf_counter() - started) * 1000)
        for key in validate_queries:
            validate_queries[key] += counter[key]
        if not valid:
            raise SessionEpochError("validation_failed", "current epoch did not validate")
        counter = {"statements": 0, "selects": 0, "writes": 0}
        started = time.perf_counter()
        epoch = rotate_epoch(path, user_id, query_counter=counter)
        rotate_times.append((time.perf_counter() - started) * 1000)
        for key in rotate_queries:
            rotate_queries[key] += counter[key]
    account_count = _account_count(path)
    return {
        "status": "measured_synthetic_copy",
        "iterations_per_operation": iterations,
        "account_count": account_count,
        "database_bytes": path.stat().st_size,
        "process_peak_rss_bytes": _peak_rss_bytes(),
        "process_peak_rss_platform_units": "bytes" if sys.platform == "darwin" else "KiB converted to bytes",
        "issue_existing_epoch": {"p50_ms": _percentile(issue_times, 0.50), "p95_ms": _percentile(issue_times, 0.95), "p99_ms": _percentile(issue_times, 0.99), **issue_queries},
        "validate_epoch": {"p50_ms": _percentile(validate_times, 0.50), "p95_ms": _percentile(validate_times, 0.95), "p99_ms": _percentile(validate_times, 0.99), **validate_queries},
        "rotate_epoch": {"p50_ms": _percentile(rotate_times, 0.50), "p95_ms": _percentile(rotate_times, 0.95), "p99_ms": _percentile(rotate_times, 0.99), **rotate_queries},
        "raw_epoch_values_emitted": False,
    }


def _account_count(database_path: Path) -> int:
    connection = _connect_rw(database_path, busy_timeout=DEFAULT_BUSY_TIMEOUT)
    try:
        _require_schema(connection)
        return int(connection.execute('SELECT COUNT(*) FROM "auth"').fetchone()[0])
    except SessionEpochError:
        raise
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        connection.close()


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inactive copy-only SQLite session epoch experiment.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-copy")
    prepare.add_argument("--source", required=True)
    prepare.add_argument("--target", required=True)
    measure = subparsers.add_parser("measure")
    measure.add_argument("--database", required=True)
    measure.add_argument("--user-id", required=True)
    measure.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        report = (prepare_auth_session_stamp_copy(args.source, args.target)
                  if args.command == "prepare-copy"
                  else measure_component(args.database, args.user_id, iterations=args.iterations))
    except SessionEpochError as error:
        print(json.dumps({"status": "refused", "error_code": error.code, "detail": error.detail}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
