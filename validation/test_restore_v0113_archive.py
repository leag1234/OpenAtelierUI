#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Tests for the fail-closed rollback archive restoration command."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from validation import restore_v0113_archive as tool


class RestoreArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / "archive"
        self.archive.mkdir(mode=0o700)
        database = sqlite3.connect(self.archive / "source.sqlite")
        database.execute("CREATE TABLE item(id TEXT PRIMARY KEY,value TEXT)")
        database.execute("INSERT INTO item VALUES('one','Community workshop')")
        database.commit()
        database.close()
        self.uploads = self.archive / "uploads"
        self.uploads.mkdir(mode=0o700)
        (self.uploads / "f1_notes.txt").write_bytes(b"synthetic notes\n")
        (self.uploads / "f2_poster.txt").write_bytes(b"festival poster\n")
        self.source_before = (self.archive / "source.sqlite").read_bytes()
        self.uploads_before = {path.name: path.read_bytes() for path in self.uploads.iterdir()}

    def test_restore_is_exact_private_and_never_activates(self):
        target = self.root / "restored"
        result = tool.restore(self.archive, target)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "restored_copy_only")
        self.assertFalse(result["cutover"])
        self.assertEqual((target / "webui.db").read_bytes(), self.source_before)
        self.assertEqual(
            {path.name: path.read_bytes() for path in (target / "uploads").iterdir()},
            self.uploads_before,
        )
        self.assertEqual(os.stat(target).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(target / "webui.db").st_mode & 0o777, 0o600)
        self.assertTrue(all((path.stat().st_mode & 0o777) == 0o600 for path in (target / "uploads").iterdir()))
        database = sqlite3.connect(target / "webui.db")
        self.assertEqual(database.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(database.execute("SELECT * FROM item").fetchall(), [("one", "Community workshop")])
        database.close()
        self.assertEqual((self.archive / "source.sqlite").read_bytes(), self.source_before)
        self.assertEqual({path.name: path.read_bytes() for path in self.uploads.iterdir()}, self.uploads_before)

    def test_existing_target_and_overlaps_are_refused(self):
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_text("unchanged")
        with self.assertRaisesRegex(tool.Refusal, "target_not_fresh"):
            tool.restore(self.archive, existing)
        self.assertEqual((existing / "keep").read_text(), "unchanged")
        with self.assertRaisesRegex(tool.Refusal, "path_overlap"):
            tool.restore(self.archive, self.archive / "nested")

    def test_symlinks_nested_uploads_and_corrupt_database_are_refused(self):
        source = self.archive / "source.sqlite"
        linked = self.root / "linked.sqlite"
        source.rename(linked)
        source.symlink_to(linked)
        with self.assertRaisesRegex(tool.Refusal, "unsafe_archive"):
            tool.restore(self.archive, self.root / "symlink-source")
        source.unlink()
        linked.rename(source)
        upload = self.uploads / "f1_notes.txt"
        moved = self.root / "moved.txt"
        upload.rename(moved)
        upload.symlink_to(moved)
        with self.assertRaisesRegex(tool.Refusal, "unsafe_archive"):
            tool.restore(self.archive, self.root / "symlink-upload")
        upload.unlink()
        moved.rename(upload)
        (self.uploads / "nested").mkdir()
        with self.assertRaisesRegex(tool.Refusal, "unsafe_archive"):
            tool.restore(self.archive, self.root / "nested-upload")
        (self.uploads / "nested").rmdir()
        source.write_bytes(b"not sqlite")
        with self.assertRaisesRegex(tool.Refusal, "corrupt_archive_database"):
            tool.restore(self.archive, self.root / "corrupt")

    def test_sidecars_resource_limits_and_relative_paths_are_refused(self):
        Path(str(self.archive / "source.sqlite") + "-wal").write_bytes(b"wal")
        with self.assertRaisesRegex(tool.Refusal, "sqlite_sidecar_unsupported"):
            tool.restore(self.archive, self.root / "sidecar")
        Path(str(self.archive / "source.sqlite") + "-wal").unlink()
        with mock.patch.object(tool, "MAX_UPLOAD_FILES", 1):
            with self.assertRaisesRegex(tool.Refusal, "upload_file_count_limit"):
                tool.restore(self.archive, self.root / "count-limit")
        with self.assertRaisesRegex(tool.Refusal, "unsafe_archive"):
            tool.restore(Path("relative"), self.root / "relative")
        with self.assertRaisesRegex(tool.Refusal, "target_not_fresh"):
            tool.restore(self.archive, Path("relative"))

    def test_archive_change_failpoint_and_publish_collision_leave_no_owned_target(self):
        target = self.root / "changed"
        original_manifest = tool._upload_manifest
        calls = 0

        def changed_manifest(path):
            nonlocal calls
            calls += 1
            result = original_manifest(path)
            if calls == 2:
                (self.uploads / "f1_notes.txt").write_bytes(b"changed")
            return result

        with mock.patch.object(tool, "_upload_manifest", side_effect=changed_manifest):
            with self.assertRaisesRegex(tool.Refusal, "archive_changed"):
                tool.restore(self.archive, target)
        self.assertFalse(target.exists())
        (self.uploads / "f1_notes.txt").write_bytes(self.uploads_before["f1_notes.txt"])

        failed = self.root / "failpoint"
        with self.assertRaisesRegex(RuntimeError, "synthetic stop"):
            tool.restore(
                self.archive,
                failed,
                failpoint=lambda stage: (_ for _ in ()).throw(RuntimeError("synthetic stop")),
            )
        self.assertFalse(failed.exists())

        raced = self.root / "raced"
        def compete(source, destination):
            Path(destination).mkdir()
            (Path(destination) / "competitor").write_text("keep")
            raise tool.Refusal("target_not_fresh")

        with mock.patch.object(tool, "_publish_directory_no_replace", side_effect=compete):
            with self.assertRaisesRegex(tool.Refusal, "target_not_fresh"):
                tool.restore(self.archive, raced)
        self.assertEqual((raced / "competitor").read_text(), "keep")
        self.assertFalse(any(self.root.glob(".raced.restore-attempt-*")))

    def test_native_publish_refuses_an_existing_empty_directory(self):
        prepared = self.root / "prepared"
        prepared.mkdir()
        target = self.root / "competitor"
        target.mkdir()
        with self.assertRaisesRegex(tool.Refusal, "target_not_fresh"):
            tool._publish_directory_no_replace(prepared, target)
        self.assertTrue(prepared.is_dir())
        self.assertTrue(target.is_dir())

    def test_cli_emits_structured_success_and_refusal(self):
        target = self.root / "cli-target"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = tool.main([
                "--archive-dir", str(self.archive),
                "--new-target-dir", str(target),
            ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "restored_copy_only")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = tool.main([
                "--archive-dir", str(self.archive),
                "--new-target-dir", str(target),
            ])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout.getvalue())["reason"], "target_not_fresh")


if __name__ == "__main__":
    unittest.main()
