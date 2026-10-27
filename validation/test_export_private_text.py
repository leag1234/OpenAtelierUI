# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic tests for the standalone private text-chat exporter."""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import export_private_text as exporter


def graph():
    nodes = {
        "question": {"id": "question", "parentId": None, "childrenIds": ["answer-a", "answer-b"], "role": "user", "content": "Choose a café — 🫖", "user_id": "owner-1", "user": {"id": "owner-1", "name": "Synthetic Owner"}},
        "answer-a": {"id": "answer-a", "parentId": "question", "childrenIds": [], "role": "assistant", "content": "Original option", "user_id": "owner-1"},
        "answer-b": {"id": "answer-b", "parentId": "question", "childrenIds": ["follow-up"], "role": "assistant", "content": "Selected option", "user_id": "owner-1"},
        "follow-up": {"id": "follow-up", "parentId": "answer-b", "childrenIds": [], "role": "user", "content": "Continue the selected plan", "user_id": "owner-1"},
    }
    return {
        "title": "Synthetic branched chat", "models": ["portable_pipe"], "params": {},
        "history": {"messages": nodes, "currentId": "follow-up"},
        "messages": [dict(nodes[key]) for key in ("question", "answer-b", "follow-up")],
        "tags": [], "timestamp": 1730000000,
    }


class ExportPrivateTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "candidate.db"
        self.output = self.root / "recovery.json"
        self._create()

    def tearDown(self):
        self.temp.cleanup()

    def _create(self, *, payload=None, meta="{}", columns="", values=None):
        if self.source.exists():
            self.source.unlink()
        db = sqlite3.connect(self.source)
        db.execute(
            "CREATE TABLE chat (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, created_at BIGINT, "
            "updated_at BIGINT, share_id TEXT, archived BOOLEAN, chat JSON, pinned BOOLEAN, meta JSON, "
            "folder_id TEXT" + columns + ")"
        )
        row = ["source-chat-id", "owner-1", "Synthetic branched chat", 1730000000, 1730000200,
               None, 0, json.dumps(payload if payload is not None else graph(), ensure_ascii=False),
               None, meta, None]
        row.extend(values or [])
        marks = ",".join("?" for _ in row)
        db.execute("INSERT INTO chat VALUES (" + marks + ")", row)
        db.commit()
        db.close()

    def _args(self, command="preview", output=None, owner="owner-1", chat="source-chat-id"):
        args = [command, "--source", str(self.source), "--owner-id", owner, "--chat-id", chat]
        if output is not None:
            args += ["--output", str(output)]
        return args

    def _run(self, *args):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = exporter.main(list(args))
        return code, json.loads(stdout.getvalue())

    def _reason(self, reason, *args):
        code, result = self._run(*args)
        self.assertEqual(code, 2)
        self.assertEqual(result, {"ok": False, "reason": reason})

    def test_preview_is_read_only_and_reports_bounded_receipt(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        code, result = self._run(*self._args())
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        receipt = result["result"]
        self.assertFalse(receipt["artifact_written"])
        self.assertEqual((receipt["chat_count"], receipt["node_count"], receipt["selected_chain_nodes"]), (1, 4, 3))
        self.assertEqual(receipt["source_sha256"], before)
        self.assertTrue(receipt["saved_work_marker_omitted"] is False)
        self.assertFalse(self.output.exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_export_preserves_branches_chain_unicode_and_private_import_shape(self):
        original = graph()
        meta = json.dumps({"_mdk_private_saved_work": {"version": 1, "last_read_at": 1730000050}})
        self._create(payload=original, meta=meta)
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        code, result = self._run(*self._args("export", self.output))
        self.assertEqual(code, 0)
        self.assertTrue(result["result"]["artifact_written"])
        self.assertTrue(result["result"]["saved_work_marker_omitted"])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        artifact = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(set(artifact), {"chats"})
        self.assertEqual(len(artifact["chats"]), 1)
        item = artifact["chats"][0]
        self.assertEqual(set(item), {"chat", "meta", "pinned", "folder_id", "current_message_id", "created_at", "updated_at"})
        self.assertEqual(item["chat"], original)
        self.assertEqual(item["current_message_id"], "follow-up")
        self.assertFalse(item["pinned"])
        self.assertEqual(item["meta"], {})
        self.assertIsNone(item["folder_id"])
        self.assertNotIn("source-chat-id", self.output.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)

    def test_owner_selection_and_private_row_guards_refuse_without_output(self):
        self._reason("chat_not_owned", *self._args("export", self.output, owner="other-owner"))
        self._reason("chat_not_found", *self._args("export", self.output, chat="missing"))
        for column, value, reason in (("share_id", "shared", "shared_or_foldered_chat"),
                                      ("folder_id", "folder", "shared_or_foldered_chat"),
                                      ("archived", 1, "archived_chat"),
                                      ("pinned", 2, "invalid_pinned_value"),
                                      ("created_at", -1, "invalid_chat_timestamp")):
            with self.subTest(column=column):
                self._create()
                db = sqlite3.connect(self.source)
                db.execute("UPDATE chat SET \"" + column + "\"=?", (value,)); db.commit(); db.close()
                self._reason(reason, *self._args("export", self.output))
                self.assertFalse(self.output.exists())

    def test_meta_marker_is_only_omitted_candidate_metadata(self):
        for raw, reason in (("{\"tags\":[\"private\"]}", "unsupported_chat_metadata"),
                            ("null", "invalid_chat_metadata"),
                            ("{\"_mdk_private_saved_work\":{\"version\":true,\"last_read_at\":null}}", "unsupported_saved_work_marker"),
                            ("{\"_mdk_private_saved_work\":{\"version\":1,\"last_read_at\":-1}}", "unsupported_saved_work_marker")):
            with self.subTest(meta=raw):
                self._create(meta=raw)
                self._reason(reason, *self._args())

    def test_optional_schema_state_must_be_empty_or_match_selection(self):
        for columns, values, reason in ((", current_message_id TEXT", ["missing"], "selected_message_mismatch"),
                                        (", variables JSON", ["{}"], "unsupported_chat_variables"),
                                        (", last_read_at BIGINT", [1730000000], "unsupported_chat_read_state")):
            with self.subTest(columns=columns):
                self._create(columns=columns, values=values)
                self._reason(reason, *self._args())
        self._create(columns=", current_message_id TEXT", values=["follow-up"])
        self.assertEqual(self._run(*self._args())[0], 0)

    def test_graph_refusals_cover_selection_connectivity_authors_and_references(self):
        mutations = (
            (lambda g: g["history"].update(currentId="absent"), "invalid_selected_message"),
            (lambda g: g["history"]["messages"]["answer-a"].update(parentId="answer-a"), "invalid_chat_graph"),
            (lambda g: g["history"]["messages"]["answer-a"].update(user_id="foreign"), "foreign_chat_author"),
            (lambda g: g["history"]["messages"]["question"].update(files=["upload"]), "unsupported_chat_reference"),
            (lambda g: g.update(tags=["tag"]), "unsupported_chat_tags"),
            (lambda g: g["history"]["messages"]["answer-b"].update(role="tool"), "unsupported_chat_content"),
            (lambda g: g["history"]["messages"]["answer-b"].update(content=["not text"]), "unsupported_chat_content"),
        )
        for change, reason in mutations:
            with self.subTest(reason=reason):
                payload = graph(); change(payload); self._create(payload=payload)
                self._reason(reason, *self._args())

    def test_json_and_unknown_schema_fail_closed(self):
        self._create(payload={"history": {"messages": {"a": {"id": "a", "role": "user", "content": "x", "parentId": None, "childrenIds": []}}, "currentId": "a"}, "messages": []})
        self._reason("flat_chain_mismatch", *self._args())
        db = sqlite3.connect(self.source)
        db.execute("UPDATE chat SET chat=?", ('{"history":{"messages":{},"currentId":"x"},"history":{}}',)); db.commit(); db.close()
        self._reason("invalid_chat_json", *self._args())
        self._create(columns=", unrecognized TEXT", values=["opaque"])
        self._reason("unsupported_chat_schema", *self._args())
        self._create(payload=json.loads('{"title":"Synthetic branched chat","history":{"messages":{"x":{"id":"x","role":"user","content":"x","parentId":null,"childrenIds":[]}},"currentId":"x"},"messages":[],"tags":[],"extra":NaN}'))
        self._reason("invalid_chat_json", *self._args())

    def test_malformed_duplicate_and_corrupt_sqlite_inputs_are_named(self):
        self._create()
        db = sqlite3.connect(self.source)
        db.execute("UPDATE chat SET chat=?", ('{"title":"x","title":"y"}',)); db.commit(); db.close()
        self._reason("invalid_chat_json", *self._args())
        self.source.write_bytes(b"not a sqlite database")
        self._reason("source_unavailable", *self._args())

    def test_composite_or_missing_row_identity_schema_refuses(self):
        self.source.unlink()
        db = sqlite3.connect(self.source)
        db.execute("CREATE TABLE chat (id TEXT, user_id TEXT, title TEXT, created_at BIGINT, updated_at BIGINT, share_id TEXT, archived BOOLEAN, chat JSON, pinned BOOLEAN, meta JSON, folder_id TEXT, PRIMARY KEY(id,user_id))")
        db.commit(); db.close()
        self._reason("ambiguous_chat_identity", *self._args())

    def test_sidecars_symlinks_and_output_collisions_are_refused(self):
        sidecar = Path(str(self.source) + "-wal")
        sidecar.write_bytes(b"synthetic")
        self._reason("source_sidecar_present", *self._args())
        sidecar.unlink()
        link = self.root / "source-link.db"
        link.symlink_to(self.source)
        code, result = self._run("preview", "--source", str(link), "--owner-id", "owner-1", "--chat-id", "source-chat-id")
        self.assertEqual((code, result), (2, {"ok": False, "reason": "source_not_regular"}))
        self._reason("output_is_source", *self._args("export", self.source))
        self.output.write_text("keep", encoding="utf-8")
        self._reason("output_not_available", *self._args("export", self.output))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep")

    def test_failed_publication_cleans_temporary_and_final_paths(self):
        with mock.patch.object(exporter.os, "link", side_effect=OSError("synthetic")):
            self._reason("output_failed", *self._args("export", self.output))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".private-chat-export-*")), [])

    def test_link_collision_preserves_competing_output(self):
        def competing_link(_source, destination):
            Path(destination).write_text("other writer", encoding="utf-8")
            raise FileExistsError("synthetic collision")
        with mock.patch.object(exporter.os, "link", side_effect=competing_link):
            self._reason("output_failed", *self._args("export", self.output))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "other writer")
        self.assertEqual(list(self.root.glob(".private-chat-export-*")), [])

    def test_cleanup_preserves_path_replaced_after_our_link(self):
        real_fsync = exporter.os.fsync
        calls = 0
        def replace_after_publish(fd):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_fsync(fd)
            self.output.unlink()
            self.output.write_text("replacement", encoding="utf-8")
            raise OSError("synthetic directory sync failure")
        with mock.patch.object(exporter.os, "fsync", side_effect=replace_after_publish):
            self._reason("output_failed", *self._args("export", self.output))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "replacement")
        self.assertEqual(list(self.root.glob(".private-chat-export-*")), [])

    def test_source_changed_during_staging_is_not_published(self):
        real_fsync = exporter.os.fsync
        changed = False
        def mutate_after_staged_write(fd):
            nonlocal changed
            result = real_fsync(fd)
            if not changed:
                changed = True
                with self.source.open("ab") as stream:
                    stream.write(b"changed")
            return result
        with mock.patch.object(exporter.os, "fsync", side_effect=mutate_after_staged_write):
            self._reason("source_changed", *self._args("export", self.output))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".private-chat-export-*")), [])

    def test_nonempty_rights_and_executable_media_shapes_refuse(self):
        for key, value, reason in (("access_control", {"read": ["other"]}, "unsupported_chat_rights"),
                                   ("tools", [{"name": "run"}], "unsupported_chat_rights"),
                                   ("type", "image", "unsupported_chat_reference")):
            with self.subTest(key=key):
                payload = graph()
                payload[key] = value
                self._create(payload=payload)
                self._reason(reason, *self._args("export", self.output))
                self.assertFalse(self.output.exists())

    def test_cli_arguments_and_content_are_not_echoed_on_refusal(self):
        secret_id = "opaque-synthetic-owner"
        self._create(payload={"history": {"messages": {}, "currentId": secret_id}})
        code, result = self._run(*self._args(owner=secret_id))
        self.assertEqual(code, 2)
        self.assertNotIn(secret_id, json.dumps(result))
        self.assertEqual(result["reason"], "chat_not_owned")
        code, result = self._run("preview", "--source", str(self.source), "--owner-id", "owner-1", "--chat-id")
        self.assertEqual(code, 2)
        self.assertEqual(result, {"ok": False, "reason": "invalid_arguments"})

    def test_standalone_cli_preview_and_export(self):
        script = Path(exporter.__file__)
        base = [sys.executable, str(script)]
        preview = subprocess.run(base + self._args(), text=True, capture_output=True, timeout=5)
        self.assertEqual(preview.returncode, 0)
        self.assertTrue(json.loads(preview.stdout)["ok"])
        exported = subprocess.run(base + self._args("export", self.output), text=True, capture_output=True, timeout=5)
        self.assertEqual(exported.returncode, 0)
        self.assertEqual(json.loads(exported.stdout)["result"]["output_bytes"], self.output.stat().st_size)
        self.assertEqual(exported.stderr, "")

    def test_size_depth_and_deadline_bounds_are_named(self):
        with mock.patch.object(exporter, "MAX_SOURCE", 1):
            self._reason("source_not_eligible", *self._args())
        deep = graph()
        cursor = deep
        for _ in range(exporter.MAX_DEPTH + 2):
            cursor["nested"] = {}
            cursor = cursor["nested"]
        self._create(payload=deep)
        self._reason("json_depth_exceeded", *self._args())
        self._create()
        with self.assertRaises(exporter.Refusal) as caught:
            exporter._read(self.source, "owner-1", "source-chat-id", time.monotonic() - 11)
        self.assertEqual(caught.exception.reason, "deadline_exceeded")

    def test_graph_node_cap_and_oversized_text_refuse(self):
        too_many = {}
        for index in range(exporter.MAX_NODES + 1):
            key = str(index)
            too_many[key] = {"id": key, "parentId": None, "childrenIds": [], "role": "user", "content": "text"}
        self._create(payload={"title": "Synthetic branched chat", "history": {"messages": too_many, "currentId": "0"}, "messages": []})
        self._reason("invalid_chat_graph", *self._args())
        oversized = graph()
        oversized["history"]["messages"]["question"]["content"] = "x" * exporter.MAX_OUTPUT
        self._create(payload=oversized)
        self._reason("input_limit_exceeded", *self._args())

    def test_output_parent_must_exist_and_output_symlink_is_not_followed(self):
        missing_parent = self.root / "missing" / "recovery.json"
        self._reason("output_not_available", *self._args("export", missing_parent))
        target = self.root / "existing-target"
        target.write_text("keep", encoding="utf-8")
        link = self.root / "output-link.json"
        link.symlink_to(target)
        self._reason("output_not_available", *self._args("export", link))
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
