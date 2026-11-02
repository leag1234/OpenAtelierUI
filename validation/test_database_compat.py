"""Regression tests for the pre-write database compatibility gate."""

from pathlib import Path
import importlib.util
import sqlite3
import tempfile
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "open_webui"
    / "utils"
    / "database_compat.py"
)
SPEC = importlib.util.spec_from_file_location("database_compat", MODULE_PATH)
database_compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(database_compat)
DatabaseMigrationRequired = database_compat.DatabaseMigrationRequired
V0113_ALEMBIC_HEAD = database_compat.V0113_ALEMBIC_HEAD
require_startup_compatible_database = (
    database_compat.require_startup_compatible_database
)


class DatabaseCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "webui.db"

    def tearDown(self):
        self.temporary.cleanup()

    def _database_with(self, head, migratehistory=False):
        connection = sqlite3.connect(self.database)
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (head,))
        if migratehistory:
            connection.execute("CREATE TABLE migratehistory (id INTEGER)")
        connection.commit()
        connection.close()

    def test_measured_v0113_database_is_refused_before_migration(self):
        self._database_with(V0113_ALEMBIC_HEAD)
        before = self.database.read_bytes()
        with self.assertRaises(DatabaseMigrationRequired):
            require_startup_compatible_database(f"sqlite:///{self.database}")
        self.assertEqual(self.database.read_bytes(), before)

    def test_owned_database_continues_to_existing_migration_path(self):
        self._database_with("candidate-head", migratehistory=True)
        require_startup_compatible_database(f"sqlite:///{self.database}")

    def test_missing_database_continues_to_initialization(self):
        require_startup_compatible_database(f"sqlite:///{self.database}")


if __name__ == "__main__":
    unittest.main()
