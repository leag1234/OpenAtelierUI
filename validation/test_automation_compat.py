import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_store():
    spec = importlib.util.spec_from_file_location(
        "automation_store_test", ROOT / "backend/open_webui/models/automations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AutomationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.path = Path(self.temp.name) / "webui.db"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE existing_fixture (id TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO existing_fixture VALUES ('keep','unchanged')")
        self.module = load_store()
        self.store = self.module.Automations("sqlite:///" + str(self.path))
        self.data = json.dumps({
            "prompt": "Create a synthetic local reminder.",
            "model_id": "synthetic-model-not-invoked",
            "rrule": "DTSTART:20990101T000000Z\nRRULE:FREQ=DAILY;COUNT=2",
            "target": {"type": "chat", "channel_id": None},
            "terminal": None,
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_create_get_and_reopen_preserve_existing_database(self):
        created = self.store.create(
            "owner", "Future reminder", None, self.data,
            json.dumps({"kind": "synthetic"}), False, 4070908800000000000,
        )
        self.assertEqual(created["name"], "Future reminder")
        self.assertFalse(created["is_active"])
        self.assertEqual(created["data"]["target"]["type"], "chat")
        reopened = self.module.Automations("sqlite:///" + str(self.path))
        self.assertEqual(reopened.get(created["id"]), created)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT * FROM existing_fixture").fetchall(), [("keep", "unchanged")])

    def test_owner_filter_query_status_and_pagination(self):
        first = self.store.create("owner", "Future reminder", None, self.data, None, False, 1)
        self.store.create("other", "Future other", None, self.data, None, True, 1)
        self.assertEqual(self.store.list("owner", query="future")[1], 1)
        self.assertEqual(self.store.list("owner", status="inactive")[0][0]["id"], first["id"])
        self.assertEqual(self.store.list("owner", status="active"), ([], 0))
        self.assertEqual(self.store.list("owner", page=2), ([], 1))

    def test_update_toggle_runs_and_delete(self):
        created = self.store.create("owner", "Original", None, self.data, None, False, 1)
        updated = self.store.update(
            created["id"], "owner", "Updated", "folder", self.data,
            json.dumps({"version": 2}), False, 2,
        )
        self.assertEqual(updated["name"], "Updated")
        self.assertEqual(updated["folder_id"], "folder")
        self.assertEqual(updated["next_run_at"], 2)
        toggled = self.store.toggle(created["id"], "owner", 3)
        self.assertTrue(toggled["is_active"])
        self.assertEqual(toggled["next_run_at"], 3)
        toggled = self.store.toggle(created["id"], "owner", 4)
        self.assertFalse(toggled["is_active"])
        self.assertIsNone(toggled["next_run_at"])
        started = self.store.mark_started(created["id"], "owner")
        self.assertIsInstance(started["last_run_at"], int)
        self.assertIsNone(self.store.mark_started(created["id"], "foreign"))
        success = self.store.record_run(created["id"], "success", chat_id="synthetic-chat")
        self.assertEqual(success["status"], "success")
        self.assertEqual(success["chat_id"], "synthetic-chat")
        failed = self.store.record_run(created["id"], "failed", error="synthetic failure")
        self.assertEqual(failed["error"], "synthetic failure")
        self.assertEqual([item["status"] for item in self.store.runs(created["id"])], ["failed", "success"])
        with self.assertRaises(ValueError):
            self.store.record_run(created["id"], "running")
        self.assertTrue(self.store.delete(created["id"], "owner"))
        self.assertIsNone(self.store.get(created["id"]))
        self.assertEqual(self.store.runs(created["id"]), [])

    def test_foreign_mutation_does_not_change_owner_row(self):
        created = self.store.create("owner", "Original", None, self.data, None, False, 1)
        self.assertIsNone(self.store.update(
            created["id"], "foreign", "Changed", None, self.data, None, True, 2
        ))
        self.assertIsNone(self.store.toggle(created["id"], "foreign", 2))
        self.assertFalse(self.store.delete(created["id"], "foreign"))
        self.assertEqual(self.store.get(created["id"])["name"], "Original")

    def test_unsupported_database_and_schema_collision_refuse(self):
        with self.assertRaises(self.module.AutomationsUnavailable):
            self.module.Automations("postgresql://example/db")
        with self.assertRaises(self.module.AutomationsUnavailable):
            self.module.Automations("sqlite:///:memory:")
        broken = Path(self.temp.name) / "broken.db"
        with closing(sqlite3.connect(broken)) as db, db:
            db.execute("CREATE TABLE openatelier_automation (id TEXT)")
        with self.assertRaises(self.module.AutomationsUnavailable):
            self.module.Automations("sqlite:///" + str(broken))


if __name__ == "__main__":
    unittest.main(verbosity=2)
