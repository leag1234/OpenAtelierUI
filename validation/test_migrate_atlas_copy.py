# SPDX-License-Identifier: BSD-3-Clause
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import migrate_atlas_copy as coordinator
from test_migrate_sqlite import build_source, build_template


def add_notes(source):
    db = sqlite3.connect(source)
    db.executescript(
        "CREATE TABLE note(id TEXT NOT NULL PRIMARY KEY,user_id TEXT,title TEXT,data JSON,meta JSON,created_at BIGINT,updated_at BIGINT);"
        "CREATE TABLE pinned_note(id TEXT NOT NULL PRIMARY KEY,user_id TEXT NOT NULL,note_id TEXT NOT NULL,created_at BIGINT NOT NULL);"
        "ALTER TABLE access_grant RENAME TO old_access_grant;"
        "CREATE TABLE access_grant(id TEXT NOT NULL PRIMARY KEY,resource_type TEXT NOT NULL,resource_id TEXT NOT NULL,principal_type TEXT NOT NULL,principal_id TEXT NOT NULL,permission TEXT NOT NULL,created_at BIGINT NOT NULL);"
        "DROP TABLE old_access_grant;"
    )
    db.execute("INSERT INTO note VALUES(?,?,?,?,?,?,?)", (
        "note-one", "u1", "Workshop plan", '{"content":{"md":"Bring the maps."}}', None, 1700000000, 1700000001
    ))
    db.execute("INSERT INTO note VALUES(?,?,?,?,?,?,?)", (
        "note-two", "u1", "Private reminder", None, "null", 1700000002, 1700000003
    ))
    db.execute("INSERT INTO pinned_note VALUES('pin-one','u1','note-two',1700000004)")
    db.commit()
    db.close()


class AtlasCopyCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.uploads, self.template = self.root / "source.db", self.root / "uploads", self.root / "template.db"
        build_source(self.source, self.uploads)
        build_template(self.template)
        add_notes(self.source)

    def _run(self, mode, label, target=None):
        return coordinator.run(mode, self.source, self.uploads, self.template,
                               self.root / (label + "-workspace"),
                               self.root / (label + "-target") if mode == "convert" else target)

    def _note_rows(self, path):
        db = sqlite3.connect(path)
        try:
            return db.execute(
                "SELECT id,user_id,title,data,meta,created_at,updated_at,is_pinned "
                "FROM openatelier_private_note ORDER BY id"
            ).fetchall()
        finally:
            db.close()

    def test_preview_keeps_full_snapshot_and_never_publishes_target(self):
        source_before = self.source.read_bytes()
        upload_before = (self.uploads / "f1_synthetic.html").read_bytes()
        template_before = self.template.read_bytes()
        result = self._run("preview", "preview")
        workspace = self.root / "preview-workspace"
        self.assertTrue(result["ok"])
        self.assertFalse(result["target_created"])
        self.assertEqual((result["note_count"], result["pinned_note_count"]), (2, 1))
        self.assertEqual((workspace / "source.sqlite").read_bytes(), source_before)
        self.assertEqual((workspace / "uploads" / "f1_synthetic.html").read_bytes(), upload_before)
        stage = sqlite3.connect(workspace / "core-staging.sqlite")
        self.assertEqual(stage.execute("SELECT COUNT(*) FROM note").fetchone()[0], 0)
        self.assertEqual(stage.execute("SELECT COUNT(*) FROM pinned_note").fetchone()[0], 0)
        stage.close()
        self.assertFalse((self.root / "preview-target").exists())
        self.assertEqual((self.root / "preview-workspace").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.source.read_bytes(), (self.uploads / "f1_synthetic.html").read_bytes(), self.template.read_bytes()),
                         (source_before, upload_before, template_before))

    def test_convert_merges_exact_note_values_and_pins_after_core_import(self):
        source_before, template_before = self.source.read_bytes(), self.template.read_bytes()
        upload_before = (self.uploads / "f1_synthetic.html").read_bytes()
        result = self._run("convert", "convert")
        target = self.root / "convert-target"
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["cutover"] is False)
        self.assertTrue(result["originals_unchanged"])
        self.assertEqual(self._note_rows(target / "webui.db"), [
            ("note-one", "u1", "Workshop plan", '{"content":{"md":"Bring the maps."}}', None, 1700000000, 1700000001, 0),
            ("note-two", "u1", "Private reminder", None, "null", 1700000002, 1700000003, 1),
        ])
        self.assertTrue(list(self.root.glob("convert-target.source-archive-*/source.sqlite")))
        self.assertEqual((self.root / "convert-workspace" / "source.sqlite").read_bytes(), source_before)
        self.assertEqual((self.source.read_bytes(), (self.uploads / "f1_synthetic.html").read_bytes(), self.template.read_bytes()),
                         (source_before, upload_before, template_before))

    def test_note_grant_refuses_before_core_stage_trim(self):
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO access_grant VALUES('grant-note','note','note-one','user','u1','read',1700000005)")
        db.commit()
        db.close()
        source_before = self.source.read_bytes()
        result = self._run("preview", "refused")
        workspace = self.root / "refused-workspace"
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "note_grants_unsupported")
        self.assertFalse((workspace / "core-staging.sqlite").exists())
        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertFalse((self.root / "refused-target").exists())

    def test_foreign_pin_refuses_before_core_stage_trim(self):
        db = sqlite3.connect(self.source)
        db.execute("UPDATE pinned_note SET user_id='other'")
        db.commit()
        db.close()
        source_before = self.source.read_bytes()
        result = self._run("preview", "foreign-pin")
        workspace = self.root / "foreign-pin-workspace"
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "orphan_pin")
        self.assertFalse((workspace / "core-staging.sqlite").exists())
        self.assertEqual(self.source.read_bytes(), source_before)


    def test_trigger_refuses_before_stage_trim(self):
        db = sqlite3.connect(self.source)
        db.execute("CREATE TRIGGER note_delete_mutates_user AFTER DELETE ON note BEGIN UPDATE user SET name='changed' WHERE id='u1'; END")
        db.commit(); db.close()
        original = self.source.read_bytes()
        result = self._run("preview", "trigger")
        workspace = self.root / "trigger-workspace"
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "source_views_or_triggers_unsupported")
        self.assertFalse((workspace / "core-staging.sqlite").exists())
        self.assertEqual(self.source.read_bytes(), original)
        db = sqlite3.connect(workspace / "source.sqlite")
        self.assertEqual(db.execute("SELECT name FROM user WHERE id='u1'").fetchone()[0], "Synthetic User")
        db.close()

    def test_disjoint_paths_reject_duplicates_and_output_input_alias(self):
        path = self.root / "same"
        for outputs, inputs in (([path, path], []), ([path], [path])):
            with self.subTest(outputs=outputs, inputs=inputs), self.assertRaises(coordinator.Refusal):
                coordinator._disjoint(outputs, inputs)

    def test_missing_notes_schema_is_a_valid_zero_note_preview(self):
        db = sqlite3.connect(self.source)
        db.execute("DROP TABLE pinned_note")
        db.execute("DROP TABLE note")
        db.commit()
        db.close()
        result = self._run("preview", "no-notes")
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["note_count"], result["pinned_note_count"]), (0, 0))
        stage = sqlite3.connect(self.root / "no-notes-workspace" / "core-staging.sqlite")
        self.assertIsNone(stage.execute("SELECT 1 FROM sqlite_master WHERE name='note'").fetchone())
        stage.close()


if __name__ == "__main__":
    unittest.main()
