# SPDX-License-Identifier: BSD-3-Clause
"""SQLite tests for the inactive copy-only account epoch component."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

UTILS = Path(__file__).resolve().parents[1] / "utils"
sys.path.insert(0, str(UTILS))
import session_epochs as epochs  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_old_auth_db(path: Path, *, with_chat: bool = True, existing_stamp: bool = False) -> None:
    db = sqlite3.connect(path)
    stamp = ", session_stamp TEXT" if existing_stamp else ""
    db.executescript(f"""
      CREATE TABLE auth (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        active INTEGER NOT NULL{stamp}
      );
      CREATE TABLE user (id TEXT PRIMARY KEY, info JSON, settings JSON);
      INSERT INTO auth(id,email,password,active) VALUES
        ('synthetic-owner','owner@example.invalid','bcrypt-placeholder',1),
        ('synthetic-other','other@example.invalid','bcrypt-placeholder',1);
      INSERT INTO user VALUES
        ('synthetic-owner','{{"keep":"owner"}}','{{"ui":{{"keep":true}}}}'),
        ('synthetic-other','{{"keep":"other"}}','{{"ui":{{"keep":false}}}}');
    """)
    if with_chat:
        db.execute("CREATE TABLE chat (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, chat JSON, meta JSON)")
        db.execute("INSERT INTO chat VALUES ('synthetic-chat','synthetic-owner','Synthetic saved work','{\"history\":{\"currentId\":\"branch-a\"}}','{}')")
    db.commit()
    db.close()


def build_ambiguous_auth_db(path: Path, *, composite_primary_key: bool) -> None:
    db = sqlite3.connect(path)
    primary = ", PRIMARY KEY (id, email)" if composite_primary_key else ""
    db.executescript(f"""
      CREATE TABLE auth (
        id VARCHAR(255) NOT NULL,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        active INTEGER NOT NULL{primary}
      );
      INSERT INTO auth VALUES
        ('synthetic-duplicate','first@example.invalid','bcrypt-placeholder',1),
        ('synthetic-duplicate','second@example.invalid','bcrypt-placeholder',1);
    """)
    db.commit()
    db.close()


class SessionEpochTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "old-source.db"
        self.target = self.root / "prepared-copy.db"
        build_old_auth_db(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        return epochs.prepare_auth_session_stamp_copy(self.source, self.target)

    def test_preparation_changes_only_fresh_copy_schema_and_preserves_source(self):
        source_hash = digest(self.source)
        source_before = sqlite3.connect(self.source)
        source_auth = source_before.execute('SELECT * FROM auth ORDER BY id').fetchall()
        source_chat = source_before.execute("SELECT * FROM chat").fetchall()
        source_before.close()

        report = self.prepare()
        self.assertEqual(report["status"], "prepared_copy")
        self.assertEqual(report["auth_account_count"], 2)
        self.assertEqual(report["added_column"], "auth.session_stamp TEXT")
        self.assertTrue(report["source_unchanged"])
        self.assertEqual(digest(self.source), source_hash)
        original = sqlite3.connect(self.source)
        target = sqlite3.connect(self.target)
        self.assertNotIn("session_stamp", {row[1] for row in original.execute('PRAGMA table_info("auth")')})
        self.assertIn("session_stamp", {row[1] for row in target.execute('PRAGMA table_info("auth")')})
        self.assertEqual(original.execute('SELECT * FROM auth ORDER BY id').fetchall(), source_auth)
        self.assertEqual(original.execute('SELECT * FROM chat').fetchall(), source_chat)
        self.assertEqual(target.execute('SELECT id,email,password,active FROM auth ORDER BY id').fetchall(), source_auth)
        self.assertEqual(target.execute("SELECT * FROM chat").fetchall(), source_chat)
        self.assertEqual(target.execute('SELECT session_stamp FROM auth ORDER BY id').fetchall(), [(None,), (None,)])
        original.close()
        target.close()

    def test_issue_is_stable_and_rotate_invalidates_only_one_existing_user(self):
        self.prepare()
        before = sqlite3.connect(self.target)
        auth_before = before.execute('SELECT id,email,password,active FROM auth ORDER BY id').fetchall()
        user_before = before.execute('SELECT * FROM user ORDER BY id').fetchall()
        chat_before = before.execute('SELECT * FROM chat').fetchall()
        before.close()

        old = epochs.issue_epoch(self.target, "synthetic-owner")
        self.assertEqual(old, epochs.issue_epoch(self.target, "synthetic-owner"))
        self.assertTrue(epochs.validate_epoch(self.target, "synthetic-owner", old))
        self.assertFalse(epochs.validate_epoch(self.target, "synthetic-owner", "other-epoch"))
        new = epochs.rotate_epoch(self.target, "synthetic-owner")
        self.assertNotEqual(old, new)
        self.assertEqual(epochs.get_epoch(self.target, "synthetic-owner"), new)
        self.assertFalse(epochs.validate_epoch(self.target, "synthetic-owner", old))
        self.assertTrue(epochs.validate_epoch(self.target, "synthetic-owner", new))
        self.assertEqual(epochs.issue_epoch(self.target, "synthetic-owner"), new)
        self.assertIsNone(epochs.get_epoch(self.target, "synthetic-other"))

        reopened = sqlite3.connect(self.target)
        self.assertEqual(reopened.execute('SELECT id,email,password,active FROM auth ORDER BY id').fetchall(), auth_before)
        stamps = reopened.execute('SELECT id,session_stamp FROM auth ORDER BY id').fetchall()
        self.assertEqual(stamps, [("synthetic-other", None), ("synthetic-owner", new)])
        self.assertEqual(reopened.execute('SELECT * FROM user ORDER BY id').fetchall(), user_before)
        self.assertEqual(reopened.execute('SELECT * FROM chat').fetchall(), chat_before)
        reopened.close()

    def test_missing_user_never_creates_auth_or_epoch_rows(self):
        self.prepare()
        before = sqlite3.connect(self.target)
        rows_before = before.execute('SELECT * FROM auth ORDER BY id').fetchall()
        before.close()
        for operation in (epochs.get_epoch, epochs.issue_epoch, epochs.rotate_epoch, epochs.validate_epoch):
            with self.subTest(operation=operation.__name__):
                args = (self.target, "missing-user", "some-epoch") if operation is epochs.validate_epoch else (self.target, "missing-user")
                with self.assertRaises(epochs.SessionEpochError) as caught:
                    operation(*args)
                self.assertEqual(caught.exception.code, "user_not_found")
        reopened = sqlite3.connect(self.target)
        self.assertEqual(reopened.execute('SELECT * FROM auth ORDER BY id').fetchall(), rows_before)
        reopened.close()

    def test_missing_database_and_unprepared_schema_are_refused_without_creation_or_repair(self):
        missing = self.root / "does-not-exist.db"
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.issue_epoch(missing, "synthetic-owner")
        self.assertEqual(caught.exception.code, "invalid_database_path")
        self.assertFalse(missing.exists())

        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.issue_epoch(self.source, "synthetic-owner")
        self.assertEqual(caught.exception.code, "session_stamp_schema_missing")
        db = sqlite3.connect(self.source)
        self.assertNotIn("session_stamp", {row[1] for row in db.execute('PRAGMA table_info("auth")')})
        db.close()

    def test_unknown_schema_corruption_and_existing_target_are_named_refusals(self):
        corrupt = self.root / "corrupt.db"
        corrupt.write_bytes(b"not sqlite")
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(corrupt, self.root / "corrupt-target.db")
        self.assertEqual(caught.exception.code, "database_corrupt")

        no_auth = self.root / "no-auth.db"
        sqlite3.connect(no_auth).close()
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(no_auth, self.root / "no-auth-target.db")
        self.assertEqual(caught.exception.code, "auth_table_missing")

        existing = self.root / "existing.db"
        existing.write_bytes(b"keep")
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(self.source, existing)
        self.assertEqual(caught.exception.code, "target_already_exists")
        self.assertEqual(existing.read_bytes(), b"keep")

        already_prepared = self.root / "already-prepared.db"
        build_old_auth_db(already_prepared, existing_stamp=True)
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(already_prepared, self.root / "already-prepared-target.db")
        self.assertEqual(caught.exception.code, "session_stamp_already_present")

    def test_copy_refuses_nonunique_or_composite_auth_identity_without_touching_inputs(self):
        for composite in (False, True):
            with self.subTest(composite_primary_key=composite):
                source = self.root / ("composite-source.db" if composite else "duplicate-source.db")
                target = self.root / ("composite-target.db" if composite else "duplicate-target.db")
                build_ambiguous_auth_db(source, composite_primary_key=composite)
                source_hash = digest(source)
                with self.assertRaises(epochs.SessionEpochError) as caught:
                    epochs.prepare_auth_session_stamp_copy(source, target)
                self.assertEqual(caught.exception.code, "auth_identity_schema_ambiguous")
                self.assertEqual(digest(source), source_hash)
                self.assertFalse(target.exists())

    def test_component_refuses_prepared_tables_without_a_sole_id_primary_key(self):
        for composite in (False, True):
            with self.subTest(composite_primary_key=composite):
                path = self.root / ("prepared-composite.db" if composite else "prepared-duplicate.db")
                primary = ", PRIMARY KEY (id, email)" if composite else ""
                db = sqlite3.connect(path)
                db.executescript(f"""
                  CREATE TABLE auth (
                    id TEXT NOT NULL,
                    email TEXT NOT NULL,
                    password TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    session_stamp TEXT{primary}
                  );
                  INSERT INTO auth VALUES
                    ('synthetic-duplicate','first@example.invalid','bcrypt-placeholder',1,'epoch-a'),
                    ('synthetic-duplicate','second@example.invalid','bcrypt-placeholder',1,'epoch-b');
                """)
                db.commit()
                db.close()
                with self.assertRaises(epochs.SessionEpochError) as caught:
                    epochs.get_epoch(path, "synthetic-duplicate")
                self.assertEqual(caught.exception.code, "auth_identity_schema_ambiguous")

    def test_varchar_id_single_primary_key_remains_supported(self):
        source = self.root / "varchar-source.db"
        target = self.root / "varchar-target.db"
        db = sqlite3.connect(source)
        db.executescript("""
          CREATE TABLE auth (
            id VARCHAR(255) PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            active INTEGER NOT NULL
          );
          INSERT INTO auth VALUES ('synthetic-owner','owner@example.invalid','bcrypt-placeholder',1);
        """)
        db.commit()
        db.close()
        epochs.prepare_auth_session_stamp_copy(source, target)
        self.assertEqual(epochs.issue_epoch(target, "synthetic-owner"), epochs.issue_epoch(target, "synthetic-owner"))

    def test_malformed_stamp_fails_closed_without_changing_row(self):
        self.prepare()
        db = sqlite3.connect(self.target)
        db.execute("UPDATE auth SET session_stamp=? WHERE id='synthetic-owner'", ("x" * 600,))
        db.commit()
        bad = db.execute("SELECT session_stamp FROM auth WHERE id='synthetic-owner'").fetchone()[0]
        db.close()

        for operation, args in (
            (epochs.get_epoch, (self.target, "synthetic-owner")),
            (epochs.issue_epoch, (self.target, "synthetic-owner")),
            (epochs.rotate_epoch, (self.target, "synthetic-owner")),
            (epochs.validate_epoch, (self.target, "synthetic-owner", "expected")),
        ):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(epochs.SessionEpochError) as caught:
                    operation(*args)
                self.assertEqual(caught.exception.code, "invalid_epoch_state")
        db = sqlite3.connect(self.target)
        self.assertEqual(db.execute("SELECT session_stamp FROM auth WHERE id='synthetic-owner'").fetchone()[0], bad)
        db.close()

    def test_unencodable_stamp_text_is_named_refusal(self):
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs._check_epoch(chr(0xD800))
        self.assertEqual(caught.exception.code, "invalid_epoch_state")

    def test_symlink_and_ambiguous_source_sidecar_are_refused(self):
        link = self.root / "source-link.db"
        link.symlink_to(self.source)
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(link, self.root / "link-target.db")
        self.assertEqual(caught.exception.code, "invalid_source_path")

        sidecar = Path(str(self.source) + "-wal")
        sidecar.write_bytes(b"synthetic sidecar")
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.prepare_auth_session_stamp_copy(self.source, self.root / "sidecar-target.db")
        self.assertEqual(caught.exception.code, "ambiguous_sqlite_state")

    def test_two_connection_write_lock_refuses_then_succeeds_after_release(self):
        self.prepare()
        old = epochs.issue_epoch(self.target, "synthetic-owner")
        holder = sqlite3.connect(self.target, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        with self.assertRaises(epochs.SessionEpochError) as caught:
            epochs.rotate_epoch(self.target, "synthetic-owner", busy_timeout=0.02)
        elapsed = time.perf_counter() - started
        self.assertEqual(caught.exception.code, "database_busy")
        self.assertLess(elapsed, 1.0)
        holder.rollback()
        holder.close()
        self.assertTrue(epochs.validate_epoch(self.target, "synthetic-owner", old))
        new = epochs.rotate_epoch(self.target, "synthetic-owner")
        self.assertFalse(epochs.validate_epoch(self.target, "synthetic-owner", old))
        self.assertTrue(epochs.validate_epoch(self.target, "synthetic-owner", new))

    def test_measurement_reports_query_counts_without_epoch_values(self):
        self.prepare()
        report = epochs.measure_component(self.target, "synthetic-owner", iterations=5)
        serialized = json.dumps(report, sort_keys=True)
        self.assertTrue(report["raw_epoch_values_emitted"] is False)
        self.assertEqual(report["iterations_per_operation"], 5)
        self.assertEqual(report["validate_epoch"]["selects"], 5)
        self.assertEqual(report["issue_existing_epoch"]["selects"], 5)
        self.assertEqual(report["rotate_epoch"]["selects"], 5)
        self.assertEqual(report["rotate_epoch"]["writes"], 5)
        self.assertNotIn("synthetic-owner", serialized)
        self.assertNotIn("session_stamp_value", serialized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
