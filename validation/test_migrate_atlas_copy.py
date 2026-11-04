# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
import json
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import migrate_atlas_copy as coordinator
from test_migrate_sqlite import add_chat_file_link, build_source, build_template
from test_openatelierui_instance import DockerDouble, IMAGE
from validation import openatelierui_instance as instance


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


def expand_realistic_copy(source, uploads):
    """Expand the minimal fixture to the size and domains reported by Atlas."""
    db = sqlite3.connect(source)
    db.execute('ALTER TABLE "user" ADD COLUMN timezone TEXT')
    for index in range(2, 11):
        user_id = f"u{index}"
        email = f"u{index}@example.invalid"
        db.execute("INSERT INTO auth VALUES (?,?,?,?,?,?)", (user_id, email, "bcrypt-placeholder", 1, None, None))
        db.execute(
            "INSERT INTO user VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, f"Synthetic User {index}", email, "user", "", "t", "t", "t", "{}", "{}", None, None, "{}", "{}", "{}", None),
        )
        db.execute('UPDATE "user" SET timezone=? WHERE id=?', (f"Etc/GMT+{index - 1}", user_id))
    for index in range(2, 127):
        chat_id = f"c{index}"
        owner = f"u{((index - 1) % 10) + 1}"
        question_id, answer_id = f"q{index}", f"a{index}"
        messages = {
            question_id: {"id": question_id, "parentId": None, "childrenIds": [answer_id], "role": "user", "content": f"Question {index}"},
            answer_id: {"id": answer_id, "parentId": question_id, "childrenIds": [], "role": "assistant", "content": f"Answer {index}"},
        }
        payload = {
            "messages": (
                [{"legacy_preview": True}]
                if index in {2, 3, 4}
                else [messages[question_id], messages[answer_id]]
            ),
            "history": {"messages": messages, "currentId": answer_id},
        }
        if index == 17:
            payload["history"]["messages"][answer_id]["usage"] = {
                "prompt_tokens": 999,
                "completion_tokens": 2,
            }
        db.execute(
            "INSERT INTO chat VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (chat_id, owner, f"Synthetic chat {index}", "t", "t", None, 0, json.dumps(payload), 0, "{}", None, "{}", "{}", None, answer_id, "{}", None),
        )
        for node_id, node in messages.items():
            db.execute(
                "INSERT INTO chat_message VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
                (f"{chat_id}-{node_id}", chat_id, owner, node["role"], node["parentId"], json.dumps(node["content"])),
            )
            if node["role"] == "assistant":
                db.execute(
                    "UPDATE chat_message SET output=?,status_history=?,usage=?,files=?,error=? WHERE id=?",
                    (
                        json.dumps({"tool": "synthetic", "result": index}),
                        json.dumps([{"action": "tool", "done": True}]),
                        json.dumps({"prompt_tokens": index, "completion_tokens": 2}),
                        json.dumps([]),
                        json.dumps({"message": "synthetic prior error"}) if index % 17 == 0 else None,
                        f"{chat_id}-{node_id}",
                    ),
                )
    for index in range(2, 47):
        file_id = f"f{index}"
        owner = f"u{((index - 1) % 10) + 1}"
        filename = f"synthetic-{index}.txt"
        blob = f"synthetic fixture {index}\n".encode()
        (uploads / f"{file_id}_{filename}").write_bytes(blob)
        meta = json.dumps({"file_hash": hashlib.sha256(blob).hexdigest(), "size": len(blob)})
        db.execute(
            "INSERT INTO file VALUES (?,?,?,?,?,NULL,?,?,?)",
            (file_id, owner, filename, meta, "t", "{}", "t", f"/app/data/uploads/{file_id}_{filename}"),
        )
    db.execute("CREATE TABLE chat_file(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,chat_id TEXT NOT NULL,file_id TEXT NOT NULL,message_id TEXT,created_at BIGINT NOT NULL,updated_at BIGINT NOT NULL)")
    for index in range(1, 2):
        chat_id, file_id = f"c{index}", f"f{index}"
        owner = "u1" if index == 1 else f"u{((index - 1) % 10) + 1}"
        message_id = "q" if index == 1 else f"q{index}"
        db.execute("INSERT INTO chat_file VALUES (?,?,?,?,?,?,?)", (f"link{index}", owner, chat_id, file_id, message_id, 10, 11))
        row = db.execute("SELECT chat FROM chat WHERE id=?", (chat_id,)).fetchone()
        payload = json.loads(row[0])
        filename = "synthetic.html" if index == 1 else f"synthetic-{index}.txt"
        payload["history"]["messages"][message_id]["files"] = [{
            "type": "file", "id": file_id, "url": file_id, "name": filename,
            "status": "uploaded", "file": {"id": file_id, "user_id": owner, "filename": filename},
        }]
        db.execute("UPDATE chat SET chat=? WHERE id=?", (json.dumps(payload), chat_id))
    db.executescript(
        'DROP TABLE IF EXISTS folder; CREATE TABLE folder(id TEXT); INSERT INTO folder VALUES("folder-one"); INSERT INTO folder VALUES("folder-two");'
        'DROP TABLE IF EXISTS shared_chat; CREATE TABLE shared_chat(id TEXT); INSERT INTO shared_chat VALUES("share-one");'
        'DROP TABLE IF EXISTS feedback; CREATE TABLE feedback(id TEXT); INSERT INTO feedback VALUES("feedback-one");'
        'DROP TABLE IF EXISTS api_key; CREATE TABLE api_key(id TEXT); INSERT INTO api_key VALUES("secret-record-not-activated");'
        'DROP TABLE IF EXISTS calendar; CREATE TABLE calendar(id TEXT); INSERT INTO calendar VALUES("calendar-one");'
        'DROP TABLE IF EXISTS "group"; CREATE TABLE "group"(id TEXT); INSERT INTO "group" VALUES("unsupported-group");'
        'DROP TABLE IF EXISTS group_member; CREATE TABLE group_member(id TEXT);'
    )
    db.execute("UPDATE chat SET folder_id='folder-one' WHERE id='c2'")
    db.execute("UPDATE chat SET share_id='share-one' WHERE id='c3'")
    db.execute("UPDATE chat SET folder_id='folder-two',share_id='share-one' WHERE id='c4'")
    db.execute("INSERT INTO access_grant VALUES ('round7-grant','tool','tool-private','user','u1','read',12)")
    first = json.loads(db.execute("SELECT chat FROM chat WHERE id='c1'").fetchone()[0])
    first["messages"] = [first["history"]["messages"][node_id] for node_id in ("q", "b")]
    db.execute("UPDATE chat SET chat=? WHERE id='c1'", (json.dumps(first),))
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

    def _run(self, mode, label, target=None, **options):
        return coordinator.run(mode, self.source, self.uploads, self.template,
                               self.root / (label + "-workspace"),
                               self.root / (label + "-target") if mode == "convert" else target, **options)

    def _add_nonfatal_blocker(self):
        db = sqlite3.connect(self.source)
        db.execute("UPDATE auth SET session_stamp='retained-session' WHERE id='u1'")
        db.commit(); db.close()

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

    def test_convert_accepts_referenced_upload_above_old_sixteen_mib_bound(self):
        payload = b"A" * (17 * 1024 * 1024)
        upload = self.uploads / "f1_synthetic.html"
        upload.write_bytes(payload)
        db = sqlite3.connect(self.source)
        db.execute(
            "UPDATE file SET meta=? WHERE id='f1'",
            (json.dumps({"file_hash": hashlib.sha256(payload).hexdigest(), "size": len(payload)}),),
        )
        db.commit()
        db.close()

        result = self._run("convert", "above-old-limit")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["target_created"])
        self.assertGreater(result["input_bytes"], 16 * 1024 * 1024)
        self.assertEqual(result["upload_bytes"], len(payload))
        self.assertEqual(result["upload_limit_bytes"], coordinator.core.MAX_UPLOAD_BYTES)
        self.assertEqual(hashlib.sha256(upload.read_bytes()).hexdigest(), hashlib.sha256(payload).hexdigest())

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


    def test_partial_preview_stays_inspectable_but_conversion_refuses_by_default(self):
        self._add_nonfatal_blocker()
        before = self.source.read_bytes()
        preview = self._run("preview", "partial-preview")
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["status"], "preview_partial")
        self.assertGreater(preview["core_blocker_count"], 0)
        refused = self._run("convert", "partial-default")
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], "partial_copy_requires_explicit_opt_in")
        self.assertFalse((self.root / "partial-default-target").exists())
        self.assertTrue((self.root / "partial-default-workspace" / "core-preview.json").exists())
        self.assertEqual(self.source.read_bytes(), before)

    def test_explicit_partial_copy_optin_keeps_archive_and_blocker_status(self):
        self._add_nonfatal_blocker()
        before = self.source.read_bytes()
        result = self._run("convert", "partial-optin", allow_partial_copy=True)
        target = self.root / "partial-optin-target"
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "converted_partial_copy_only")
        self.assertTrue(result["partial_copy_opt_in"])
        self.assertIs(result["cutover"], False)
        self.assertTrue(result["originals_unchanged"])
        archive = next(self.root.glob("partial-optin-target.source-archive-*/source.sqlite"))
        archived = sqlite3.connect(archive)
        self.assertEqual(archived.execute("SELECT session_stamp FROM auth WHERE id='u1'").fetchone()[0], "retained-session")
        archived.close()
        self.assertEqual(len(self._note_rows(target / "webui.db")), 2)
        self.assertEqual(self.source.read_bytes(), before)

    def test_realistic_auxiliary_domains_are_archived_without_blocking_core_copy(self):
        expand_realistic_copy(self.source, self.uploads)
        before = self.source.read_bytes()
        preview = self._run("preview", "realistic-aux")
        self.assertTrue(preview["ok"], preview)
        core_preview = json.loads((self.root / "realistic-aux-workspace" / "core-preview.json").read_text())
        codes = {item["code"] for item in core_preview["blockers"]}
        for code in (
            "unsupported_nonempty_domain_folder", "unsupported_nonempty_domain_shared_chat",
            "unsupported_nonempty_domain_feedback", "unsupported_nonempty_domain_api_key",
            "unsupported_nonempty_domain_calendar", "source_schema_mismatch_group",
        ):
            self.assertIn(code, codes)
        self.assertEqual(core_preview["user_timezones"]["preserved_count"], 9)
        self.assertEqual(len(core_preview["blockers"]), 15)
        self.assertEqual(
            sum(item["code"] == "chat_preview_rebuilt_from_selected_graph"
                for item in core_preview["blockers"]),
            3,
        )
        self.assertEqual(
            sum(item["code"] == "private_chat_metadata_requires_unshared_unfoldered_chat"
                for item in core_preview["blockers"]),
            3,
        )
        self.assertEqual(
            sum(item["code"] == "private_chat_metadata_message_field_mismatch_usage"
                for item in core_preview["blockers"]),
            1,
        )
        self.assertFalse(core_preview["fatal"])
        result = self._run("convert", "realistic-aux-convert", allow_partial_copy=True)
        self.assertTrue(result["ok"], result)
        target = sqlite3.connect(self.root / "realistic-aux-convert-target" / "webui.db")
        self.assertEqual(target.execute("SELECT COUNT(*) FROM user").fetchone()[0], 10)
        self.assertEqual(target.execute("SELECT COUNT(*) FROM chat").fetchone()[0], 126)
        self.assertEqual(target.execute("SELECT COUNT(*) FROM file").fetchone()[0], 46)
        self.assertEqual(target.execute("SELECT folder_id,share_id FROM chat WHERE id='c2'").fetchone(), (None, None))
        self.assertEqual(target.execute("SELECT folder_id,share_id FROM chat WHERE id='c3'").fetchone(), (None, None))
        self.assertEqual(target.execute("SELECT folder_id,share_id FROM chat WHERE id='c4'").fetchone(), (None, None))
        timezone_rows = target.execute(
            "SELECT settings FROM user WHERE id<>'u1' ORDER BY id"
        ).fetchall()
        self.assertEqual(len(timezone_rows), 9)
        self.assertTrue(all(json.loads(row[0]).get("timezone") for row in timezone_rows))
        copied = json.loads(target.execute("SELECT chat FROM chat WHERE id='c17'").fetchone()[0])
        answer = copied["history"]["messages"]["a17"]
        self.assertEqual(answer["statusHistory"], [{"action": "tool", "done": True}])
        self.assertEqual(answer["usage"], {"prompt_tokens": 999, "completion_tokens": 2})
        self.assertEqual(answer["error"], {"message": "synthetic prior error"})
        target.close()
        archive = next(self.root.glob("realistic-aux-convert-target.source-archive-*/source.sqlite"))
        archived = sqlite3.connect(archive)
        self.assertEqual(archived.execute("SELECT COUNT(*) FROM api_key").fetchone()[0], 1)
        self.assertEqual(
            json.loads(archived.execute(
                "SELECT usage FROM chat_message WHERE id='c17-a17'"
            ).fetchone()[0]),
            {"prompt_tokens": 17, "completion_tokens": 2},
        )
        archived.close()
        self.assertEqual(self.source.read_bytes(), before)

    def test_cli_reports_concrete_existing_workspace_reason(self):
        existing = self.root / "existing-workspace"
        existing.mkdir()
        script = Path(coordinator.__file__)
        completed = subprocess.run([
            sys.executable, str(script), "preview", "--source-db", str(self.source),
            "--uploads-root", str(self.uploads), "--empty-old-target-template", str(self.template),
            "--workspace-dir", str(existing),
        ], text=True, capture_output=True)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(completed.stdout)["reason"], "output_exists_or_unsafe")


    def test_cli_partial_conversion_requires_and_accepts_explicit_flag(self):
        self._add_nonfatal_blocker()
        script = Path(coordinator.__file__)
        base = [sys.executable, str(script), "convert", "--source-db", str(self.source),
                "--uploads-root", str(self.uploads), "--empty-old-target-template", str(self.template)]
        refused = subprocess.run(base + ["--workspace-dir", str(self.root / "cli-refused-ws"),
            "--new-target-dir", str(self.root / "cli-refused-target")], text=True, capture_output=True)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stdout)["reason"], "partial_copy_requires_explicit_opt_in")
        accepted = subprocess.run(base + ["--workspace-dir", str(self.root / "cli-allowed-ws"),
            "--new-target-dir", str(self.root / "cli-allowed-target"), "--allow-partial-copy"],
            text=True, capture_output=True)
        self.assertEqual(accepted.returncode, 0, accepted.stdout)
        self.assertEqual(json.loads(accepted.stdout)["status"], "converted_partial_copy_only")

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

    def test_converted_target_can_be_started_as_the_direct_data_copy(self):
        with sqlite3.connect(self.template) as db:
            db.execute('DROP TABLE auth')
            db.execute('CREATE TABLE auth(id TEXT NOT NULL PRIMARY KEY, email TEXT, password TEXT, active INTEGER)')
        result = self._run("convert", "handoff")
        target = self.root / "handoff-target"
        self.assertTrue(result["ok"], result)
        original_db = (target / "webui.db").read_bytes()
        original_upload = (target / "uploads" / "f1_synthetic.html").read_bytes()
        old_static, frontend = self.root / "old-static", self.root / "frontend-static"
        old_static.mkdir(); frontend.mkdir()
        (old_static / "base.css").write_text("base")
        (frontend / "app.js").write_text("app")
        env = self.root / "operator.env"
        env.write_text("WEBUI_SECRET_KEY=synthetic-only\n"); env.chmod(0o600)
        workspace = self.root / "handoff-instance"
        args = types.SimpleNamespace(
            image="openatelier-test:local", expected_image_id=IMAGE,
            source_data=str(target), env_file=str(env), workspace=str(workspace),
            network="openatelierui-private", host_port=18080, container_port=8080,
            health_timeout=5,
        )
        docker = DockerDouble(old_static, frontend)
        with patch.object(instance.subprocess, "run", side_effect=docker), \
                patch.object(instance, "_health"), \
                patch.object(instance.os, "getuid", return_value=1000), \
                patch.object(instance.os, "getgid", return_value=1000):
            started = instance.start(args)
        self.assertEqual(started["url"], "http://127.0.0.1:18080")
        self.assertEqual((workspace / "pre-session" / "webui.db").read_bytes(), original_db)
        self.assertEqual((workspace / "data" / "uploads" / "f1_synthetic.html").read_bytes(), original_upload)
        self.assertEqual((target / "webui.db").read_bytes(), original_db)
        self.assertEqual((target / "uploads" / "f1_synthetic.html").read_bytes(), original_upload)


if __name__ == "__main__":
    unittest.main()
