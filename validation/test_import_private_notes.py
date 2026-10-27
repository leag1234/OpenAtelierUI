# SPDX-License-Identifier: BSD-3-Clause
"""Real-SQLite tests for the isolated private-notes import prototype."""

import json
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from validation import import_private_notes as tool


def database(path, target=False, role="user"):
    db = sqlite3.connect(path)
    db.executescript("CREATE TABLE user(id TEXT,email TEXT,role TEXT);"
                     "INSERT INTO user VALUES('owner','owner@example.test','" + role + "');")
    if target:
        db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO settings VALUES('kept','exact')")
    else:
        db.executescript("CREATE TABLE note(id TEXT NOT NULL PRIMARY KEY,user_id TEXT,title TEXT,data JSON,meta JSON,created_at BIGINT,updated_at BIGINT);"
                         "CREATE TABLE pinned_note(id TEXT NOT NULL PRIMARY KEY,user_id TEXT NOT NULL,note_id TEXT NOT NULL,created_at BIGINT NOT NULL);"
                         "CREATE TABLE access_grant(id TEXT NOT NULL PRIMARY KEY,resource_type TEXT NOT NULL,resource_id TEXT NOT NULL,principal_type TEXT NOT NULL,principal_id TEXT NOT NULL,permission TEXT NOT NULL,created_at BIGINT NOT NULL);")
        db.execute("INSERT INTO note VALUES(?,?,?,?,?,?,?)", ("n1", "owner", "First", '{"content":{"md":"hello"}}', "null", 10, 11))
        db.execute("INSERT INTO note VALUES(?,?,?,?,?,?,?)", ("n2", "owner", "Second", None, None, 12, 13))
        db.execute("INSERT INTO pinned_note VALUES('p1','owner','n2',14)")
        db.execute("INSERT INTO access_grant VALUES('g1','tool','t1','user','other','read',15)")
    db.commit()
    db.close()


class PrivateNotesImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.target = self.root / "source.db", self.root / "target.db"
        database(self.source)
        database(self.target, target=True)

    def contents(self, path):
        return path.read_bytes()

    def test_preview_and_convert_preserve_values_and_inputs(self):
        source_before, target_before = self.contents(self.source), self.contents(self.target)
        preview = tool.run("preview", self.source, self.target)
        self.assertEqual((preview["note_count"], preview["pinned_note_count"]), (2, 1))
        self.assertNotIn("owner@example.test", json.dumps(preview))
        self.assertNotIn("hello", json.dumps(preview))
        self.assertEqual(source_before, self.contents(self.source))
        self.assertEqual(target_before, self.contents(self.target))
        output = self.root / "converted.db"
        result = tool.run("convert", self.source, self.target, output)
        self.assertFalse(result["cutover"])
        self.assertEqual(result["output_sha256"], tool._hash_file(output, __import__("time").monotonic())[1])
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        db = sqlite3.connect(output)
        rows = db.execute("SELECT id,user_id,title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note ORDER BY id").fetchall()
        self.assertEqual(rows, [
            ("n1", "owner", "First", '{"content":{"md":"hello"}}', "null", 10, 11, 0),
            ("n2", "owner", "Second", None, None, 12, 13, 1),
        ])
        self.assertEqual(db.execute("SELECT * FROM settings").fetchall(), [("kept", "exact")])
        db.close()
        self.assertEqual(source_before, self.contents(self.source))
        self.assertEqual(target_before, self.contents(self.target))

    def test_admin_owner_is_supported_but_pending_is_not(self):
        self.source.unlink()
        self.target.unlink()
        database(self.source, role="admin")
        database(self.target, target=True, role="admin")
        self.assertTrue(tool.run("preview", self.source, self.target)["eligible"])
        self.source.unlink()
        database(self.source, role="pending")
        with self.assertRaises(tool.Refusal):
            tool.run("preview", self.source, self.target)

    def test_grants_and_foreign_pins_refuse_without_output(self):
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO access_grant VALUES('g2','note','n1','user','owner','read',16)")
        db.commit()
        db.close()
        output = self.root / "refused.db"
        with self.assertRaises(tool.Refusal):
            tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())
        self.source.unlink()
        database(self.source)
        db = sqlite3.connect(self.source)
        db.execute("UPDATE pinned_note SET user_id='other' WHERE id='p1'")
        db.commit()
        db.close()
        with self.assertRaises(tool.Refusal):
            tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())

    def test_mismatch_and_invalid_json_refuse(self):
        self.target.unlink()
        database(self.target, target=True, role="admin")
        with self.assertRaises(tool.Refusal):
            tool.run("preview", self.source, self.target)

    def test_json_duplicate_depth_and_size_are_bounded(self):
        for raw in ('{"x":1,"x":2}', '{"x":' * 66 + '0' + '}' * 66, '{"x":"' + 'a' * tool.MAX_JSON_TEXT + '"}'):
            db = sqlite3.connect(self.source)
            db.execute("UPDATE note SET data=? WHERE id='n1'", (raw,))
            db.commit()
            db.close()
            with self.assertRaises(tool.Refusal):
                tool.run("preview", self.source, self.target)

    def test_late_malformed_note_and_unsafe_output_leave_no_result(self):
        db = sqlite3.connect(self.source)
        db.execute("UPDATE note SET data='[]' WHERE id='n2'")
        db.commit()
        db.close()
        output = self.root / "late-invalid.db"
        with self.assertRaises(tool.Refusal):
            tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())
        self.source.unlink()
        database(self.source)
        existing = self.root / "existing.db"
        existing.write_bytes(b"keep")
        with self.assertRaises(tool.Refusal):
            tool.run("convert", self.source, self.target, existing)
        self.assertEqual(existing.read_bytes(), b"keep")
        alias = self.root / "source-link.db"
        alias.symlink_to(self.source)
        with self.assertRaises(tool.Refusal):
            tool.run("preview", alias, self.target)
        self.target.unlink()
        database(self.target, target=True)
        db = sqlite3.connect(self.source)
        db.execute("UPDATE note SET data='{" + '"x":NaN' + "}' WHERE id='n1'")
        db.commit()
        db.close()
        with self.assertRaises(tool.Refusal):
            tool.run("preview", self.source, self.target)

    def test_source_change_after_staging_prevents_publication(self):
        output = self.root / "changed.db"
        original = tool._assert_bundle_unchanged
        def mutate_then_check(entries, started):
            with self.source.open("ab") as stream:
                stream.write(b"changed")
            original(entries, started)
        with mock.patch.object(tool, "_assert_bundle_unchanged", side_effect=mutate_then_check):
            with self.assertRaises(tool.Refusal):
                tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())

    def test_output_race_never_removes_competing_file(self):
        output = self.root / "raced.db"
        def compete(temp, dest):
            Path(dest).write_bytes(b"competitor")
            raise FileExistsError()
        with mock.patch.object(tool.os, "link", side_effect=compete):
            with self.assertRaises(tool.Refusal):
                tool.run("convert", self.source, self.target, output)
        self.assertEqual(output.read_bytes(), b"competitor")

    def test_replacement_after_link_is_not_unlinked(self):
        output = self.root / "replaced.db"
        link = os.link
        def replace(temp, dest):
            link(temp, dest)
            Path(dest).unlink()
            Path(dest).write_bytes(b"replacement")
        with mock.patch.object(tool.os, "link", side_effect=replace):
            with self.assertRaises(tool.Refusal):
                tool.run("convert", self.source, self.target, output)
        self.assertEqual(output.read_bytes(), b"replacement")

    def test_directory_sync_failure_removes_only_our_publication(self):
        output = self.root / "sync-failed.db"
        real_open = os.open
        def fail_directory(path, flags, *args):
            if Path(path) == output.parent and flags == os.O_RDONLY:
                self.assertTrue(output.exists())
                raise OSError("synthetic directory sync failure")
            return real_open(path, flags, *args)
        with mock.patch.object(tool.os, "open", side_effect=fail_directory):
            with self.assertRaisesRegex(tool.Refusal, "publication_failed"):
                tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())

    def test_cli_preview_emits_bounded_json(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            result = tool.main(["preview", "--source-db", str(self.source), "--target-db", str(self.target)])
        self.assertEqual(result, 0)
        self.assertTrue(json.loads(stdout.getvalue())["eligible"])

    def test_target_sidecar_is_refused(self):
        sidecar = Path(str(self.target) + "-journal")
        sidecar.write_bytes(b"pending")
        with self.assertRaises(tool.Refusal):
            tool.run("preview", self.source, self.target)

    def test_reserved_index_collision_refuses(self):
        db = sqlite3.connect(self.target)
        db.execute("CREATE INDEX openatelier_private_note_owner_time ON settings(key)")
        db.commit()
        db.close()
        with self.assertRaises(tool.Refusal):
            tool.run("convert", self.source, self.target, self.root / "collision.db")
        self.assertFalse((self.root / "collision.db").exists())

    def test_new_source_sidecar_is_detected_before_publication(self):
        original = tool._assert_bundle_unchanged
        def add_wal(entries, started):
            Path(str(self.source) + "-wal").write_bytes(b"new sidecar")
            original(entries, started)
        output = self.root / "sidecar-race.db"
        with mock.patch.object(tool, "_assert_bundle_unchanged", side_effect=add_wal):
            with self.assertRaises(tool.Refusal):
                tool.run("convert", self.source, self.target, output)
        self.assertFalse(output.exists())

    def test_source_bundle_copy_includes_present_wal_and_shm(self):
        entries = tool._bundle_snapshot(self.source, __import__("time").monotonic())
        for suffix, payload in (("-wal", b"synthetic WAL"), ("-shm", b"synthetic SHM")):
            Path(str(self.source) + suffix).write_bytes(payload)
        entries = tool._bundle_snapshot(self.source, __import__("time").monotonic())
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            copied = tool._open_source_copy(self.source, entries, Path(directory), __import__("time").monotonic())
            for suffix in ("", "-wal", "-shm"):
                self.assertEqual(Path(str(copied) + suffix).read_bytes(), Path(str(self.source) + suffix).read_bytes())


if __name__ == "__main__":
    unittest.main()
