# SPDX-License-Identifier: BSD-3-Clause
"""Synthetic local tests for attached-text bundle export."""
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_attached_text as bundle
import export_private_text as core


OWNER = "owner-1"
FILE_ID = "file-7"
FILE_NAME = "workshop.txt"
FILE_BYTES = "A café plan — bring tea.\n".encode("utf-8")


def text_graph():
    nodes = {
        "question": {"id": "question", "parentId": None, "childrenIds": ["answer-a", "answer-b"], "role": "user", "content": "Choose the workshop plan", "user_id": OWNER, "user": {"id": OWNER, "name": "Synthetic Owner"}},
        "answer-a": {"id": "answer-a", "parentId": "question", "childrenIds": [], "role": "assistant", "content": "First option", "user_id": OWNER},
        "answer-b": {"id": "answer-b", "parentId": "question", "childrenIds": ["follow-up"], "role": "assistant", "content": "Selected option", "user_id": OWNER},
        "follow-up": {"id": "follow-up", "parentId": "answer-b", "childrenIds": [], "role": "user", "content": "Continue the chosen plan", "user_id": OWNER},
    }
    digest = hashlib.sha256(FILE_BYTES).hexdigest()
    meta = {"name": FILE_NAME, "content_type": "text/plain", "size": len(FILE_BYTES), "data": {}, "file_hash": digest}
    file = {"created_at": 1730000000, "data": {}, "filename": FILE_NAME, "hash": None, "id": FILE_ID, "meta": meta, "updated_at": 1730000100, "user_id": OWNER}
    ref = {"error": "", "file": file, "id": FILE_ID, "itemId": "upload-item-1", "name": FILE_NAME, "size": len(FILE_BYTES), "status": "uploaded", "type": "file", "url": "/api/v1/files/" + FILE_ID}
    nodes["question"]["files"] = [json.loads(json.dumps(ref))]
    flat = [dict(nodes[key]) for key in ("question", "answer-b", "follow-up")]
    flat[0]["files"] = [json.loads(json.dumps(ref))]
    return {"title": "Synthetic branched chat", "models": ["portable_pipe"], "params": {}, "history": {"messages": nodes, "currentId": "follow-up"}, "messages": flat, "files": [ref], "tags": [], "timestamp": 1730000000}


class ExportAttachedTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "closed-copy.db"
        self.uploads = self.root / "uploads"
        self.uploads.mkdir()
        self.output = self.root / "bundle.zip"
        self._create()

    def tearDown(self):
        self.temp.cleanup()

    def _create(self, *, payload=None, filename=FILE_NAME, path=None, meta=None, data=None, acl=None, raw=FILE_BYTES):
        if self.source.exists():
            self.source.unlink()
        file_meta = meta if meta is not None else {"name": filename, "content_type": "text/plain", "size": len(raw), "data": {}, "file_hash": hashlib.sha256(raw).hexdigest()}
        graph = text_graph() if payload is None else payload
        db = sqlite3.connect(self.source)
        db.execute("CREATE TABLE chat (id VARCHAR(255), user_id VARCHAR(255), title TEXT, created_at DATETIME, updated_at DATETIME, share_id VARCHAR(255), archived INTEGER, chat JSON, pinned BOOLEAN, meta JSON, folder_id TEXT)")
        db.execute('CREATE UNIQUE INDEX "chat id unique" ON chat(id)')
        db.execute("INSERT INTO chat VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("source-chat-id", OWNER, "Synthetic branched chat", 1730000000, 1730000200, None, 0, json.dumps(graph, ensure_ascii=False), None, "{}", None))
        db.execute("CREATE TABLE file (id TEXT, user_id TEXT, filename TEXT, path TEXT, hash TEXT, meta JSON, data JSON, created_at INTEGER, updated_at BIGINT, access_control JSON)")
        db.execute('CREATE UNIQUE INDEX "file id unique" ON file(id)')
        stored_path = path if path is not None else FILE_ID + "_" + filename
        db.execute("INSERT INTO file VALUES (?,?,?,?,?,?,?,?,?,?)", (FILE_ID, OWNER, filename, stored_path, None, json.dumps(file_meta, ensure_ascii=False), json.dumps({} if data is None else data), 1730000000, 1730000100, None if acl is None else json.dumps(acl)))
        db.commit(); db.close()
        dest = self.uploads / Path(stored_path).name
        dest.write_bytes(raw)

    def _run(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = bundle.main(list(args))
        return code, json.loads(output.getvalue())

    def _args(self, command="preview", output=None, owner=OWNER, chat="source-chat-id"):
        args = [command, "--source", str(self.source), "--uploads-root", str(self.uploads), "--owner-id", owner, "--chat-id", chat]
        if output is not None:
            args += ["--output", str(output)]
        return args

    def _refusal(self, reason, *args):
        code, result = self._run(*args)
        self.assertEqual((code, result), (2, {"ok": False, "reason": reason}))

    def test_preview_and_export_preserve_graph_and_build_exact_two_member_zip(self):
        expected_graph = text_graph()
        for ref in (expected_graph["files"][0], expected_graph["history"]["messages"]["question"]["files"][0], expected_graph["messages"][0]["files"][0]):
            ref.update(id=bundle.TOKEN, url="openatelier-attachment:" + bundle.TOKEN, file={"attachment_token": bundle.TOKEN})
        source_before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        code, preview = self._run(*self._args())
        self.assertEqual(code, 0)
        self.assertFalse(preview["result"]["artifact_written"])
        self.assertFalse(self.output.exists())
        receipt_text = json.dumps(preview)
        for private in (str(self.source), str(self.uploads), OWNER, "source-chat-id", FILE_ID, FILE_NAME, FILE_BYTES.decode("utf-8")):
            self.assertNotIn(private, receipt_text)
        code, receipt = self._run(*self._args("export", self.output))
        self.assertEqual(code, 0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_before)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.namelist(), ["manifest.json", "attachment.txt"])
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(archive.read("attachment.txt"), FILE_BYTES)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(set(manifest), {"format", "chat_import", "attachment"})
        self.assertEqual(manifest["format"], bundle.FORMAT)
        self.assertNotIn("source-chat-id", json.dumps(manifest))
        self.assertNotIn(FILE_ID, json.dumps(manifest))
        self.assertNotIn(OWNER, json.dumps(manifest["attachment"]["source_file_metadata"]))
        self.assertEqual(manifest["attachment"]["source_file_metadata"]["filename"], FILE_NAME)
        item = manifest["chat_import"]["chats"][0]
        graph = item["chat"]
        self.assertEqual(graph, expected_graph)
        self.assertEqual(graph["history"]["currentId"], "follow-up")
        self.assertEqual([x["id"] for x in graph["messages"]], ["question", "answer-b", "follow-up"])
        for ref in (graph["files"][0], graph["history"]["messages"]["question"]["files"][0], graph["messages"][0]["files"][0]):
            self.assertEqual((ref["id"], ref["url"], ref["file"]), (bundle.TOKEN, "openatelier-attachment:" + bundle.TOKEN, {"attachment_token": bundle.TOKEN}))
        self.assertEqual(receipt["result"]["attachment_sha256"], hashlib.sha256(FILE_BYTES).hexdigest())
        self.assertEqual(receipt["result"]["bundle_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_same_source_produces_deterministic_archive_bytes(self):
        first = self.root / "first.zip"
        second = self.root / "second.zip"
        self.assertEqual(self._run(*self._args("export", first))[0], 0)
        self.assertEqual(self._run(*self._args("export", second))[0], 0)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_measured_runtime_upload_prefix_is_mapped_under_explicit_copy_root(self):
        self._create(path="/app/backend/data/uploads/" + FILE_ID + "_" + FILE_NAME)
        code, result = self._run(*self._args())
        self.assertEqual(code, 0)
        self.assertFalse(result["result"]["artifact_written"])

    def test_missing_foreign_and_unsafe_file_paths_refuse_without_artifact(self):
        self._refusal("chat_not_owned", *self._args(owner="other-owner"))
        self._create(path="../outside.txt")
        self._refusal("unsafe_upload_path", *self._args())
        self._create(path="/tmp/other/uploads/" + FILE_ID + "_" + FILE_NAME)
        self._refusal("unsafe_upload_path", *self._args())
        self._create(path="file-7_workshop.txt")
        target = self.uploads / "file-7_workshop.txt"
        target.unlink(); target.symlink_to(self.source)
        self._refusal("attached_file_unavailable", *self._args())
        linked_root = self.root / "uploads-link"
        linked_root.symlink_to(self.uploads, target_is_directory=True)
        self._refusal("unsafe_upload_root", "preview", "--source", str(self.source), "--uploads-root", str(linked_root), "--owner-id", OWNER, "--chat-id", "source-chat-id")
        self.assertFalse(self.output.exists())

    def test_unknown_file_schema_and_nonprivate_acl_refuse(self):
        db = sqlite3.connect(self.source); db.execute("ALTER TABLE file ADD COLUMN unexpected TEXT"); db.commit(); db.close()
        self._refusal("unsupported_file_schema", *self._args())
        self._create(acl={"users": {"other-user": ["read"]}})
        self._refusal("attached_file_unavailable", *self._args())
        self._create(data={"unexpected": "nonempty"})
        self._refusal("attached_file_unavailable", *self._args())
        self._create(meta={"name": FILE_NAME, "content_type": "application/pdf", "size": len(FILE_BYTES), "data": {}, "file_hash": hashlib.sha256(FILE_BYTES).hexdigest()})
        self._refusal("attached_file_unavailable", *self._args())
        db = sqlite3.connect(self.source); db.execute('DROP INDEX "file id unique"'); db.commit(); db.close()
        self._refusal("ambiguous_file_identity", *self._args())

    def test_foreign_file_row_and_unsafe_header_filename_refuse(self):
        db = sqlite3.connect(self.source); db.execute('UPDATE file SET user_id=?', ("other-owner",)); db.commit(); db.close()
        self._refusal("attached_file_unavailable", *self._args())
        unsafe = 'unsafe";name.txt'
        graph = text_graph()
        for ref in (graph["files"][0], graph["history"]["messages"]["question"]["files"][0], graph["messages"][0]["files"][0]):
            ref["name"] = unsafe
            ref["file"]["filename"] = unsafe
            ref["file"]["meta"]["name"] = unsafe
        self._create(payload=graph, filename=unsafe)
        self._refusal("unsafe_upload_path", *self._args())

    def test_oversize_and_deadline_refuse_before_artifact(self):
        graph = text_graph()
        for ref in (graph["files"][0], graph["history"]["messages"]["question"]["files"][0], graph["messages"][0]["files"][0]):
            ref["size"] = bundle.MAX_FILE + 1
            ref["file"]["meta"]["size"] = bundle.MAX_FILE + 1
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        self._create()
        with mock.patch.object(bundle.time, "monotonic", return_value=core.DEADLINE + 1):
            with self.assertRaises(core.Refusal) as raised:
                bundle._bundle(self.source, self.uploads, OWNER, "source-chat-id", 0)
        self.assertEqual(raised.exception.reason, "deadline_exceeded")
        self.assertFalse(self.output.exists())

    def test_missing_or_mismatched_row_metadata_and_bytes_refuse(self):
        self._create(meta={"name": FILE_NAME, "content_type": "text/plain", "size": len(FILE_BYTES) + 1, "data": {}})
        self._refusal("attached_file_unavailable", *self._args())
        self._create(meta={"name": FILE_NAME, "content_type": "text/plain", "size": len(FILE_BYTES), "data": {}, "file_hash": "0" * 64})
        self._refusal("attached_file_hash_mismatch", *self._args())
        invalid_bytes = b"\xff" + b"X" * (len(FILE_BYTES) - 1)
        payload = text_graph()
        digest = hashlib.sha256(invalid_bytes).hexdigest()
        refs = [payload["files"][0], payload["history"]["messages"]["question"]["files"][0], payload["messages"][0]["files"][0]]
        for ref in refs:
            ref["file"]["meta"]["file_hash"] = digest
        self._create(payload=payload, raw=invalid_bytes)
        self._refusal("attached_file_not_utf8", *self._args())

    def test_unknown_multiple_or_inconsistent_references_refuse_before_output(self):
        graph = text_graph(); graph["history"]["messages"]["answer-b"]["files"] = [{"id": "second"}]
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        graph = text_graph(); graph["files"].append(dict(graph["files"][0]))
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        graph = text_graph(); graph["messages"][0]["files"][0]["name"] = "spoof.txt"
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        graph = text_graph(); graph["history"]["messages"]["question"]["files"][0]["id"] = "other-file"
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        graph = text_graph(); graph["files"][0]["name"] = 'unsafe";name.txt'
        self._create(payload=graph)
        self._refusal("unsupported_chat_reference", *self._args())
        self.assertFalse(self.output.exists())

    def test_missing_file_row_is_nondisclosing_and_diagnostics_omit_paths(self):
        db = sqlite3.connect(self.source); db.execute('DELETE FROM file'); db.commit(); db.close()
        code, result = self._run(*self._args())
        self.assertEqual((code, result), (2, {"ok": False, "reason": "attached_file_unavailable"}))
        self.assertNotIn(str(self.source), json.dumps(result))
        self.assertNotIn(str(self.uploads), json.dumps(result))

    def test_upload_mutation_after_temp_fsync_is_refused_and_cleaned(self):
        real_fsync = os.fsync
        replaced = False
        path = self.uploads / (FILE_ID + "_" + FILE_NAME)
        replacement = self.uploads / "replacement"
        def replace_after_temp_fsync(fd):
            nonlocal replaced
            real_fsync(fd)
            if not replaced:
                replaced = True
                replacement.write_bytes(FILE_BYTES)
                replacement.replace(path)
        with mock.patch.object(core.os, "fsync", side_effect=replace_after_temp_fsync):
            self._refusal("attached_file_changed", *self._args("export", self.output))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".private-chat-export-*")), [])

    def test_output_collision_is_preserved(self):
        self.output.write_bytes(b"other writer")
        self._refusal("output_not_available", *self._args("export", self.output))
        self.assertEqual(self.output.read_bytes(), b"other writer")


if __name__ == "__main__":
    unittest.main()
