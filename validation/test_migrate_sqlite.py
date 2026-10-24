#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Named, dependency-free tests for the bounded SQLite importer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from migrate_sqlite import MigrationError, convert, preview


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_source(path: Path, uploads: Path, *, blocked: bool = False, bad_chat: bool = False) -> None:
    uploads.mkdir()
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE alembic_version(version_num TEXT);
      CREATE TABLE migratehistory(id INTEGER PRIMARY KEY, name TEXT);
      CREATE TABLE auth(id TEXT, email TEXT, password TEXT, active INTEGER, mfa TEXT, session_stamp TEXT);
      CREATE TABLE user(id TEXT, name TEXT, email TEXT, role TEXT, profile_image_url TEXT, created_at TEXT, updated_at TEXT, last_active_at TEXT, settings TEXT, info TEXT, username TEXT, bio TEXT, oauth TEXT, scim TEXT, variables TEXT);
      CREATE TABLE chat(id TEXT, user_id TEXT, title TEXT, created_at TEXT, updated_at TEXT, share_id TEXT, archived INTEGER, chat TEXT, pinned INTEGER, meta TEXT, folder_id TEXT, tasks TEXT, summary TEXT, last_read_at TEXT, current_message_id TEXT, variables TEXT, timer_at TEXT);
      CREATE TABLE chat_message(id TEXT, chat_id TEXT, user_id TEXT, role TEXT, parent_id TEXT, content TEXT, output TEXT, model_id TEXT, files TEXT, sources TEXT, embeds TEXT, done TEXT, status_history TEXT, error TEXT, usage TEXT, created_at TEXT, updated_at TEXT, context_summary TEXT, meta TEXT);
      CREATE TABLE file(id TEXT, user_id TEXT, filename TEXT, meta TEXT, created_at TEXT, hash TEXT, data TEXT, updated_at TEXT, path TEXT);
      CREATE TABLE function(id TEXT, user_id TEXT, name TEXT, type TEXT, content TEXT, meta TEXT, created_at TEXT, updated_at TEXT, valves TEXT, is_active INTEGER, is_global INTEGER, version_id TEXT);
      CREATE TABLE function_history(id TEXT, function_id TEXT, content TEXT);
      CREATE TABLE config(key TEXT, value TEXT);
      CREATE TABLE access_grant(id TEXT);
      CREATE TABLE "group"(id TEXT);
      CREATE TABLE group_member(id TEXT);
    """)
    db.execute("INSERT INTO alembic_version VALUES ('current-synthetic')")
    db.execute("INSERT INTO migratehistory VALUES (1, 'current')")
    db.execute("INSERT INTO auth VALUES ('u1','u1@example.invalid','bcrypt-placeholder',1,NULL,?)", ('stamp' if blocked else None,))
    db.execute("INSERT INTO user VALUES ('u1','Synthetic User','u1@example.invalid','user','', 't','t','t','{}','{}',NULL,NULL,'{}','{}','{}')")
    messages = {
        "q": {"id":"q", "parentId":None, "childrenIds":["a","b"], "role":"user", "content":"Question"},
        "a": {"id":"a", "parentId":"q", "childrenIds":[], "role":"assistant", "content":"Answer A"},
        "b": {"id":"b", "parentId":"q", "childrenIds":[], "role":"assistant", "content":"Answer B"},
    }
    payload = {"messages":[{"id":"q","role":"user","content":"Question"}], "history":{"messages":messages,"currentId":"b"}}
    if bad_chat:
        payload["history"]["messages"]["b"]["content"] = "mismatch"
    db.execute("INSERT INTO chat VALUES ('c1','u1','Synthetic chat','t','t',NULL,0,?,0,'{}',NULL,'{}','{}',?,?, '{}',NULL)", (json.dumps(payload), 't' if blocked else None, 'b'))
    for node_id, node in messages.items():
        if bad_chat and node_id == "b":
            continue
        db.execute("INSERT INTO chat_message VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
                   (f'c1-{node_id}', 'c1', 'u1', node['role'], node['parentId'], json.dumps(node['content'])))
    blob = b"<html>synthetic fixture</html>"
    upload = uploads / "f1_synthetic.html"
    upload.write_bytes(blob)
    meta = json.dumps({"file_hash":hashlib.sha256(blob).hexdigest(),"size":len(blob)})
    db.execute("INSERT INTO file VALUES ('f1','u1','synthetic.html',?,'t',NULL,'{}','t','/app/data/uploads/f1_synthetic.html')", (meta,))
    db.execute("INSERT INTO function VALUES ('fn1','u1','Synthetic','pipe','def pipe(): return 1','{}','t','t','{}',1,0,?)", ('v1' if blocked else None,))
    if blocked:
        db.execute("INSERT INTO config VALUES ('synthetic','{}')")
        db.execute("INSERT INTO function_history VALUES ('h1','fn1','old')")
    db.commit()
    db.close()


def build_template(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE alembic_version(version_num TEXT);
      CREATE TABLE migratehistory(id INTEGER PRIMARY KEY, name TEXT);
      CREATE TABLE auth(id TEXT, email TEXT, password TEXT, active INTEGER);
      CREATE TABLE user(id TEXT, name TEXT, email TEXT, role TEXT, profile_image_url TEXT, api_key TEXT, created_at TEXT, updated_at TEXT, last_active_at TEXT, settings TEXT, info TEXT, oauth_sub TEXT);
      CREATE TABLE chat(id TEXT, user_id TEXT, title TEXT, created_at TEXT, updated_at TEXT, share_id TEXT, archived INTEGER, chat TEXT, pinned INTEGER, meta TEXT, folder_id TEXT);
      CREATE TABLE file(id TEXT, user_id TEXT, filename TEXT, meta TEXT, created_at TEXT, hash TEXT, data TEXT, updated_at TEXT, path TEXT, access_control TEXT);
      CREATE TABLE function(id TEXT, user_id TEXT, name TEXT, type TEXT, content TEXT, meta TEXT, created_at TEXT, updated_at TEXT, valves TEXT, is_active INTEGER, is_global INTEGER);
      INSERT INTO alembic_version VALUES ('old-version');
      INSERT INTO migratehistory VALUES (1,'old-migration');
    """)
    db.commit()
    db.close()


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.db'
        self.template = self.root / 'template.db'
        self.uploads = self.root / 'source-uploads'
        build_source(self.source, self.uploads)
        build_template(self.template)

    def tearDown(self):
        self.temp.cleanup()

    def test_eligible_preview_is_read_only_and_reports_known_schema(self):
        hashes = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertTrue(report['cutover_eligible'])
        self.assertEqual(hashes, (digest(self.source), digest(self.template)))
        self.assertEqual(report['table_counts']['function'], 1)

    def test_blocked_domains_are_reported_and_do_not_claim_cutover(self):
        # Add deliberately unsupported semantics to an otherwise valid fixture.
        db = sqlite3.connect(self.source)
        db.execute("UPDATE auth SET session_stamp='retained-session'")
        db.execute("UPDATE chat SET last_read_at='unmapped'")
        db.execute("UPDATE function SET version_id='v1'")
        db.execute("INSERT INTO config VALUES ('x','{}')")
        db.execute("INSERT INTO function_history VALUES ('h','fn1','old')")
        db.commit(); db.close()
        report = preview(self.source, self.uploads, self.template)
        codes = {block['code'] for block in report['blockers']}
        self.assertFalse(report['cutover_eligible'])
        self.assertIn('unsupported_nonempty_domain_config', codes)
        self.assertIn('unsupported_nonempty_domain_function_history', codes)
        self.assertIn('session_stamp_not_preserved', codes)
        self.assertTrue(any(code.startswith('unsupported_chat_field_') for code in codes))
        self.assertIn('function_revision_link_not_preserved', codes)

    def test_fresh_target_and_separate_exact_source_archive(self):
        target = self.root / 'candidate'
        source_hash, template_hash = digest(self.source), digest(self.template)
        report = convert(self.source, self.uploads, self.template, target)
        self.assertTrue(report['cutover_eligible'])
        self.assertEqual(digest(self.source), source_hash)
        self.assertEqual(digest(self.template), template_hash)
        archive = Path(report['archive']['path'])
        self.assertNotEqual(archive, target)
        self.assertFalse(archive.is_relative_to(target))
        self.assertEqual(digest(archive / 'source.sqlite'), source_hash)
        self.assertEqual((archive / 'uploads' / 'f1_synthetic.html').read_bytes(), (self.uploads / 'f1_synthetic.html').read_bytes())
        db = sqlite3.connect(target / 'webui.db')
        self.assertEqual(db.execute('SELECT id,password FROM auth').fetchone(), ('u1','bcrypt-placeholder'))
        self.assertEqual(db.execute('SELECT id FROM user').fetchone(), ('u1',))
        self.assertEqual(db.execute('SELECT id FROM chat').fetchone(), ('c1',))
        chat_json = json.loads(db.execute('SELECT chat FROM chat WHERE id=?', ('c1',)).fetchone()[0])
        self.assertEqual(chat_json['history']['currentId'], 'b')
        self.assertEqual(set(chat_json['history']['messages']), {'q', 'a', 'b'})
        self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone(), ('old-version',))
        self.assertEqual(db.execute('SELECT id,content FROM function').fetchone(), ('fn1','def pipe(): return 1'))
        file_row = db.execute('SELECT path FROM file').fetchone()[0]
        self.assertEqual(file_row, '/app/backend/data/uploads/f1_synthetic.html')
        self.assertEqual((target / 'uploads' / 'f1_synthetic.html').read_bytes(), (self.uploads / 'f1_synthetic.html').read_bytes())
        db.close()
        self.assertEqual(json.loads((target / 'report.json').read_text())['target_dir'], str(target.resolve()))

    def test_inconsistent_chat_refuses_without_touching_inputs(self):
        self.tearDown()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source, self.template, self.uploads = self.root/'source.db', self.root/'template.db', self.root/'uploads'
        build_source(self.source, self.uploads, bad_chat=True)
        build_template(self.template)
        before = digest(self.source), digest(self.template)
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, self.root/'candidate')
        self.assertEqual(caught.exception.code, 'fatal_source_input')
        self.assertFalse((self.root/'candidate').exists())
        self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_interrupted_attempt_is_retained_and_retry_is_fresh(self):
        target = self.root/'candidate'
        before = digest(self.source), digest(self.template)
        def stop(stage):
            if stage == 'after_rows_before_commit':
                raise RuntimeError('injected interruption')
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target, failpoint=stop)
        failed = Path(caught.exception.report['failed_attempt_dir'])
        self.assertTrue((failed/'failure.json').is_file())
        self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), before)
        report = convert(self.source, self.uploads, self.template, target)
        self.assertEqual(report['status'], 'prepared_for_independent_review')

    def test_existing_target_is_never_overwritten(self):
        target = self.root/'existing'
        target.mkdir(); (target/'sentinel').write_text('keep')
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target)
        self.assertEqual(caught.exception.code, 'target_already_exists')
        self.assertEqual((target/'sentinel').read_text(), 'keep')

    def test_missing_upload_is_fatal_and_inputs_remain_unchanged(self):
        before = digest(self.source), digest(self.template)
        (self.uploads/'f1_synthetic.html').unlink()
        report = preview(self.source, self.uploads, self.template)
        self.assertTrue(report['fatal'])
        self.assertIn('uploaded_file_missing_or_unsafe', {item['code'] for item in report['blockers']})
        self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_duplicate_chat_id_is_fatal(self):
        db = sqlite3.connect(self.source)
        original = db.execute("SELECT * FROM chat WHERE id='c1'").fetchone()
        placeholders = ','.join('?' for _ in original)
        db.execute(f'INSERT INTO chat VALUES ({placeholders})', original)
        db.commit(); db.close()
        report = preview(self.source, self.uploads, self.template)
        codes = {item['code'] for item in report['blockers']}
        self.assertTrue(report['fatal'])
        self.assertIn('duplicate_chat_id', codes)

    def test_security_state_mfa_grants_and_groups_are_fatal(self):
        for sql, expected in [
            ("UPDATE auth SET mfa='enabled'", 'mfa_state_unsupported'),
            ("INSERT INTO access_grant VALUES ('grant')", 'security_domain_nonempty_access_grant'),
            ("INSERT INTO \"group\" VALUES ('g')", 'security_domain_nonempty_group'),
            ("INSERT INTO group_member VALUES ('m')", 'security_domain_nonempty_group_member'),
        ]:
            with self.subTest(expected=expected):
                db = sqlite3.connect(self.source); db.execute(sql); db.commit(); db.close()
                before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report['fatal'])
                self.assertIn(expected, {item['code'] for item in report['blockers']})
                self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_unknown_schema_template_trigger_and_wal_sidecar_fail_closed(self):
        db = sqlite3.connect(self.source); db.execute('CREATE TABLE surprise(id TEXT)'); db.commit(); db.close()
        before = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('unknown_source_tables', {item['code'] for item in report['blockers']})
        self.assertEqual((digest(self.source), digest(self.template)), before)
        db = sqlite3.connect(self.source); db.execute('DROP TABLE surprise'); db.commit(); db.close()
        db = sqlite3.connect(self.template); db.execute('CREATE TRIGGER unsafe AFTER INSERT ON auth BEGIN SELECT 1; END'); db.commit(); db.close()
        before = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('unsafe_old_template_objects', {item['code'] for item in report['blockers']})
        self.assertEqual((digest(self.source), digest(self.template)), before)
        db = sqlite3.connect(self.template); db.execute('DROP TRIGGER unsafe'); db.commit(); db.close()
        Path(str(self.source) + '-wal').write_bytes(b'uncheckpointed')
        with self.assertRaises(MigrationError) as caught:
            preview(self.source, self.uploads, self.template)
        self.assertEqual(caught.exception.code, 'ambiguous_sqlite_state')

    def test_message_owner_mismatch_and_chat_cycle_are_fatal(self):
        db = sqlite3.connect(self.source); db.execute("UPDATE chat_message SET user_id='other' WHERE id='c1-q'"); db.commit(); db.close()
        before = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('normalized_message_owner_mismatch', {item['code'] for item in report['blockers']})
        self.assertEqual((digest(self.source), digest(self.template)), before)
        db = sqlite3.connect(self.source)
        row = db.execute("SELECT chat FROM chat WHERE id='c1'").fetchone()[0]
        payload = json.loads(row); nodes = payload['history']['messages']
        nodes['q']['parentId'] = 'b'; nodes['b']['childrenIds'] = ['q']
        db.execute("UPDATE chat SET chat=? WHERE id='c1'", (json.dumps(payload),))
        db.execute("UPDATE chat_message SET parent_id='b' WHERE id='c1-q'")
        db.execute("UPDATE chat_message SET parent_id='q' WHERE id='c1-b'")
        db.commit(); db.close()
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('inconsistent_chat_graph', {item['code'] for item in report['blockers']})
        db = sqlite3.connect(self.source); db.execute("UPDATE chat_message SET chat_id='orphan' WHERE id='c1-q'"); db.commit(); db.close()
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('orphan_normalized_message', {item['code'] for item in report['blockers']})

    def test_auth_user_email_mismatch_and_traversing_upload_path_are_fatal(self):
        db = sqlite3.connect(self.source); db.execute("UPDATE user SET email='other@example.invalid' WHERE id='u1'"); db.commit(); db.close()
        before = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('auth_user_email_mismatch', {item['code'] for item in report['blockers']})
        self.assertEqual((digest(self.source), digest(self.template)), before)
        db = sqlite3.connect(self.source); db.execute("UPDATE user SET email='u1@example.invalid' WHERE id='u1'"); db.execute("UPDATE file SET path='/../../f1_synthetic.html'"); db.commit(); db.close()
        report = preview(self.source, self.uploads, self.template)
        self.assertIn('unsafe_file_path', {item['code'] for item in report['blockers']})

    def test_corrupt_database_is_json_refusal_without_traceback(self):
        corrupt = self.root/'corrupt.db'; corrupt.write_bytes(b'not a sqlite database')
        script = Path(__file__).with_name('migrate_sqlite.py')
        result = subprocess.run([sys.executable, str(script), 'preview', '--source-db', str(corrupt), '--uploads-root', str(self.uploads), '--empty-old-target-template', str(self.template)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('Traceback', result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['error_code'], 'sqlite_database_error')
        self.assertEqual(corrupt.read_bytes(), b'not a sqlite database')


if __name__ == '__main__':
    unittest.main(verbosity=2)
