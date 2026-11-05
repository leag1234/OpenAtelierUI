# SPDX-License-Identifier: BSD-3-Clause
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))
import migrate_sqlite
from test_migrate_sqlite import build_source, build_template


class ModelUpdateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "backend/open_webui/utils/model_compat.py"
        spec = importlib.util.spec_from_file_location("model_compat", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_body_id_is_accepted_and_matching_query_is_preserved(self):
        self.assertEqual(self.module.resolve_model_update_id(None, "body-id"), "body-id")
        self.assertEqual(self.module.resolve_model_update_id("same", "same"), "same")

    def test_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.resolve_model_update_id("query-id", "body-id")


class FolderMigrationSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.uploads = self.root / "uploads"
        self.template = self.root / "template.db"
        build_source(self.source, self.uploads)
        build_template(self.template)

    def add_folder(self, identity="folder", parent=None, owner="u1", *, items="[]", meta="{}", data="{}"):
        db = sqlite3.connect(self.source)
        db.execute(
            "INSERT INTO folder VALUES (?,?,?,?,?,?,?,?,?,?)",
            (identity, parent, owner, identity, items, meta, data, 0, 10, 11),
        )
        db.commit()
        db.close()

    def blocker_codes(self):
        report = migrate_sqlite.preview(self.source, self.uploads, self.template)
        return {entry["code"] for entry in report["blockers"]}

    def test_missing_owner_and_parent_are_fatal(self):
        self.add_folder(owner="missing-user", parent="missing-parent")
        codes = self.blocker_codes()
        self.assertIn("folder_owner_missing", codes)
        self.assertIn("folder_parent_missing", codes)

    def test_parent_cycle_is_fatal(self):
        self.add_folder("a", "b")
        self.add_folder("b", "a")
        self.assertIn("folder_parent_cycle", self.blocker_codes())

    def test_cross_owner_chat_reference_is_fatal(self):
        self.add_folder(owner="u1")
        db = sqlite3.connect(self.source)
        db.execute("UPDATE folder SET user_id='other-user'")
        db.execute("UPDATE chat SET folder_id='folder'")
        db.commit()
        db.close()
        codes = self.blocker_codes()
        self.assertIn("folder_owner_missing", codes)
        self.assertIn("chat_folder_owner_mismatch", codes)

    def test_v0113_json_null_and_data_are_preserved(self):
        folder_data = {"files": ["f1"], "system_prompt": "Synthetic prompt", "model_ids": ["model-private"]}
        self.add_folder(items="null", meta="null", data=json.dumps(folder_data))
        db = sqlite3.connect(self.source)
        db.execute("UPDATE chat SET folder_id='folder' WHERE id='c1'")
        db.commit()
        db.close()
        report = migrate_sqlite.convert(self.source, self.uploads, self.template, self.root / "target")
        self.assertFalse(report["fatal"])
        target = sqlite3.connect(self.root / "target" / "webui.db")
        row = target.execute("SELECT items,meta,data FROM folder WHERE id='folder'").fetchone()
        target.close()
        self.assertEqual(row[0:2], (None, None))
        self.assertEqual(json.loads(row[2]), folder_data)

    def test_legacy_nine_column_folder_shape_remains_supported(self):
        db = sqlite3.connect(self.source)
        db.execute("ALTER TABLE folder RENAME TO folder_current")
        db.execute("CREATE TABLE folder(id TEXT PRIMARY KEY,parent_id TEXT,user_id TEXT,name TEXT,items JSON,meta JSON,is_expanded BOOLEAN,created_at BIGINT,updated_at BIGINT)")
        db.execute("DROP TABLE folder_current")
        db.execute("INSERT INTO folder VALUES ('legacy',NULL,'u1','Legacy','[]','{}',0,10,11)")
        db.commit()
        db.close()
        self.assertNotIn("source_schema_mismatch_folder", self.blocker_codes())

    def test_unknown_folder_column_is_fatal(self):
        db = sqlite3.connect(self.source)
        db.execute("ALTER TABLE folder ADD COLUMN unknown_future_value TEXT")
        db.commit()
        db.close()
        self.assertIn("source_schema_mismatch_folder", self.blocker_codes())

    def test_scalar_folder_data_is_fatal(self):
        self.add_folder(data='"not-an-object"')
        self.assertIn("invalid_folder_data", self.blocker_codes())

    def test_malformed_folder_data_is_fatal(self):
        self.add_folder(data='{"files": [}')
        self.assertIn("invalid_folder_data", self.blocker_codes())

    def test_oversized_folder_data_is_fatal(self):
        self.add_folder(data=json.dumps({"value": "x" * migrate_sqlite.MAX_FOLDER_JSON_BYTES}))
        self.assertIn("invalid_folder_data", self.blocker_codes())


class BrowserContractSourceTests(unittest.TestCase):
    def test_account_trigger_and_i18n_paths_are_present(self):
        menu = (ROOT / "src/lib/components/layout/Sidebar/UserMenu.svelte").read_text()
        self.assertEqual(menu.count('DropdownMenu.Trigger aria-haspopup="menu"'), 1)
        stable = ROOT / "static/static/openatelierui/i18n.js"
        legacy = ROOT / "static/_app/immutable/chunks/DKxluP3e.js"
        self.assertTrue(stable.is_file())
        self.assertIn("export { i18n as i }", stable.read_text())
        self.assertIn("/static/openatelierui/i18n.js", legacy.read_text())


if __name__ == "__main__":
    unittest.main()
