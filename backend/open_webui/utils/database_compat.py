"""Fail closed before legacy migrations can mutate an incompatible database."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from urllib.parse import unquote


V0113_ALEMBIC_HEAD = "d4c1a8e37b62"


class DatabaseMigrationRequired(RuntimeError):
    """The database must be converted on a copy before application startup."""


def require_startup_compatible_database(database_url: str) -> None:
    """Refuse external SQLite migration lineages before any migration writes.

    Missing files and databases already owned by this application remain on the
    normal initialization path. The measured v0.11.3 lineage gets its specific
    copy-only instruction; unknown or newer external lineages fail closed.
    """

    prefixes = ("sqlite:///", "sqlite+pysqlite:///")
    prefix = next((item for item in prefixes if database_url.startswith(item)), None)
    if prefix is None:
        return
    encoded_path = database_url[len(prefix) :].partition("?")[0]
    if not encoded_path:
        return
    path = Path(unquote(encoded_path))
    if not path.is_absolute() or not path.is_file():
        return

    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "alembic_version" not in tables or "migratehistory" in tables:
            return
        heads = {
            row[0]
            for row in connection.execute(
                "SELECT version_num FROM alembic_version"
            )
        }
    finally:
        connection.close()

    if heads == {V0113_ALEMBIC_HEAD}:
        raise DatabaseMigrationRequired(
            "This v0.11.3 database requires copy-only conversion before startup. "
            "Keep the original unchanged and run openatelierui-migrate-v0113."
        )
    raise DatabaseMigrationRequired(
        "This external SQLite database has an unsupported migration lineage. "
        "Keep the original unchanged and use a supported copy-only conversion "
        "before startup."
    )
