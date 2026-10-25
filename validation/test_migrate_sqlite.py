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
from unittest import mock
from pathlib import Path

import migrate_sqlite as importer
from migrate_sqlite import MigrationError, convert, preview


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def marker_tool_source(marker: Path) -> str:
    return f"def pipe(**kwargs):\n    open({str(marker)!r}, 'w').write('executed')\n    return 'synthetic'\n"


def build_source(path: Path, uploads: Path, *, blocked: bool = False, bad_chat: bool = False) -> None:
    uploads.mkdir()
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE alembic_version(version_num TEXT);
      CREATE TABLE migratehistory(id INTEGER PRIMARY KEY, name TEXT);
      CREATE TABLE auth(id TEXT, email TEXT, password TEXT, active INTEGER, mfa TEXT, session_stamp TEXT);
      CREATE TABLE user(id TEXT, name TEXT, email TEXT, role TEXT, profile_image_url TEXT, created_at TEXT, updated_at TEXT, last_active_at TEXT, settings TEXT, info TEXT, username TEXT, bio TEXT, oauth TEXT, scim TEXT, variables TEXT);
      CREATE TABLE chat(id TEXT, user_id TEXT, title TEXT, created_at TEXT, updated_at TEXT, share_id TEXT, archived INTEGER, chat TEXT, pinned INTEGER, meta TEXT, folder_id TEXT, tasks TEXT, summary TEXT, last_read_at INTEGER, current_message_id TEXT, variables TEXT, timer_at TEXT);
      CREATE TABLE chat_message(id TEXT, chat_id TEXT, user_id TEXT, role TEXT, parent_id TEXT, content TEXT, output TEXT, model_id TEXT, files TEXT, sources TEXT, embeds TEXT, done TEXT, status_history TEXT, error TEXT, usage TEXT, created_at TEXT, updated_at TEXT, context_summary TEXT, meta TEXT);
      CREATE TABLE file(id TEXT, user_id TEXT, filename TEXT, meta TEXT, created_at TEXT, hash TEXT, data TEXT, updated_at TEXT, path TEXT);
      CREATE TABLE function(id TEXT, user_id TEXT, name TEXT, type TEXT, content TEXT, meta TEXT, created_at TEXT, updated_at TEXT, valves TEXT, is_active INTEGER, is_global INTEGER, version_id TEXT);
      CREATE TABLE function_history(id TEXT, function_id TEXT, content TEXT);
      CREATE TABLE tool(id TEXT, user_id TEXT, name TEXT, content TEXT, specs TEXT, meta TEXT, valves TEXT, updated_at INTEGER, created_at INTEGER, version_id TEXT);
      CREATE TABLE tool_history(id TEXT, tool_id TEXT, parent_id TEXT, snapshot TEXT, user_id TEXT, commit_message TEXT, created_at INTEGER);
      CREATE TABLE model(id TEXT, user_id TEXT, base_model_id TEXT, name TEXT, params TEXT, meta TEXT, updated_at INTEGER, created_at INTEGER, is_active INTEGER, version_id TEXT);
      CREATE TABLE model_history(id TEXT, model_id TEXT, parent_id TEXT, snapshot TEXT, user_id TEXT, commit_message TEXT, created_at INTEGER);
      CREATE TABLE config(key TEXT, value TEXT, updated_at DATETIME);
      CREATE TABLE access_grant(id TEXT, resource_type TEXT, resource_id TEXT, principal_type TEXT, principal_id TEXT, permission TEXT, created_at INTEGER);
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
    inert_content = marker_tool_source(path.parent / 'tool-executed-marker')
    db.execute("INSERT INTO tool VALUES ('tool-private','u1','Synthetic private tool',?, '[{\"name\":\"run\"}]', '{\"description\":\"synthetic\",\"manifest\":{}}', NULL, 10, 9, NULL)", (inert_content,))
    db.execute("INSERT INTO model VALUES ('model-private','u1','portable_pipe','Synthetic private model','{}','{}',10,9,1,NULL)")
    if blocked:
        db.execute("INSERT INTO config(key,value) VALUES ('synthetic','{}')")
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
      CREATE TABLE tool(id TEXT, user_id TEXT, name TEXT, content TEXT, specs TEXT, meta TEXT, valves TEXT, access_control TEXT, updated_at INTEGER, created_at INTEGER);
      CREATE TABLE model(id TEXT, user_id TEXT, base_model_id TEXT, name TEXT, params TEXT, meta TEXT, access_control TEXT, is_active INTEGER, updated_at INTEGER, created_at INTEGER);
      CREATE TABLE config(id INTEGER NOT NULL PRIMARY KEY, data JSON NOT NULL, version INTEGER NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
      INSERT INTO alembic_version VALUES ('old-version');
      INSERT INTO migratehistory VALUES (1,'old-migration');
    """)
    db.commit()
    db.close()


def add_group_import_schema(source: Path, template: Path, *, target_group: bool = True) -> None:
    db = sqlite3.connect(source)
    db.execute('DROP TABLE "group_member"')
    db.execute('DROP TABLE "group"')
    db.executescript('''
      CREATE TABLE "group"(
        id TEXT, user_id TEXT, name TEXT, description TEXT, data TEXT, meta TEXT,
        permissions TEXT, created_at INTEGER, updated_at INTEGER, parent_group_id TEXT
      );
      CREATE TABLE group_member(
        id TEXT, group_id TEXT, user_id TEXT, created_at INTEGER, updated_at INTEGER
      );
    ''')
    db.commit()
    db.close()
    if target_group:
        db = sqlite3.connect(template)
        db.execute('''CREATE TABLE "group"(
          id TEXT PRIMARY KEY, user_id TEXT, name TEXT, description TEXT, data JSON,
          meta JSON, permissions JSON, user_ids JSON, created_at INTEGER, updated_at INTEGER
        )''')
        db.commit()
        db.close()


def insert_flat_group(source: Path, *, user_id: str = "u1", group_id: str = "g1") -> None:
    db = sqlite3.connect(source)
    db.execute(
        'INSERT INTO "group" VALUES (?,?,?,?,?,?,?,?,?,NULL)',
        (group_id, "u1", "Synthetic read group", "Flat read group fixture",
         '{"config":{"share":"members"}}', None, "{}", 100, 101),
    )
    db.execute("INSERT INTO group_member VALUES ('gm1',?,?,100,101)", (group_id, user_id))
    db.commit()
    db.close()


def add_user(source: Path, user_id: str, *, name: str | None = None) -> None:
    db = sqlite3.connect(source)
    db.execute("INSERT INTO auth VALUES (?,?,?,1,NULL,NULL)", (user_id, user_id + "@example.invalid", "bcrypt-placeholder"))
    db.execute(
        "INSERT INTO user VALUES (?,?,?,'user','', 't','t','t','{}','{}',NULL,NULL,'{}','{}','{}')",
        (user_id, name or "Synthetic " + user_id, user_id + "@example.invalid"),
    )
    db.commit()
    db.close()


def named_tool_connection(principal_id: str = "u1") -> dict:
    return {
        "url": "http://127.0.0.1:18081",
        "path": "openapi.json",
        "type": "openapi",
        "spec_type": "url",
        "spec": "",
        "auth_type": "none",
        "key": "",
        "config": {
            "enable": True,
            "access_grants": [
                {
                    "principal_type": "user",
                    "principal_id": principal_id,
                    "permission": "read",
                }
            ],
        },
        "info": {
            "id": "synthetic_openapi",
            "name": "Synthetic OpenAPI tool fixture",
            "description": "Bounded synthetic tool server",
        },
        "headers": None,
        "forward_cookies": False,
    }


def add_named_tool_configuration(source: Path, connection: dict | str | None = None, *, other_rows: int = 0) -> None:
    if isinstance(connection, str):
        value = connection
    else:
        selected = connection if connection is not None else named_tool_connection()
        value = json.dumps(selected if isinstance(selected, list) else [selected])
    db = sqlite3.connect(source)
    db.execute(
        "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
        ("tool_server.connections", value),
    )
    for index in range(other_rows):
        db.execute(
            "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
            (f"synthetic-archived-{index}", "{}"),
        )
    db.commit()
    db.close()


def add_second_private_chat(source: Path) -> None:
    """Add a bounded five-node branched conversation with a known owner."""
    db = sqlite3.connect(source)
    db.execute("INSERT INTO auth VALUES ('u2','u2@example.invalid','bcrypt-placeholder',1,NULL,NULL)")
    db.execute("INSERT INTO user VALUES ('u2','Synthetic Second User','u2@example.invalid','user','', 't','t','t','{}','{}',NULL,NULL,'{}','{}','{}')")
    nodes = {
        "question": {"id": "question", "parentId": None, "childrenIds": ["answer-a", "answer-b"], "role": "user", "content": "Choose a synthetic venue", "timestamp": 1730000000},
        "answer-a": {"id": "answer-a", "parentId": "question", "childrenIds": ["follow-up"], "role": "assistant", "content": "Original venue", "timestamp": 1730000001, "user_id": "u2"},
        "answer-b": {"id": "answer-b", "parentId": "question", "childrenIds": [], "role": "assistant", "content": "Alternative venue", "timestamp": 1730000002, "user_id": "u2"},
        "follow-up": {"id": "follow-up", "parentId": "answer-a", "childrenIds": ["continuation"], "role": "user", "content": "Continue that plan", "timestamp": 1730000003},
        "continuation": {"id": "continuation", "parentId": "follow-up", "childrenIds": [], "role": "assistant", "content": "Useful continuation", "timestamp": 1730000004, "user_id": "u2"},
    }
    payload = {
        "title": "Synthetic branched conversation", "models": ["portable_pipe"], "params": {},
        "history": {"messages": nodes, "currentId": "continuation"},
        "messages": [dict(nodes[node_id]) for node_id in ("question", "answer-a", "follow-up", "continuation")],
        "tags": [], "timestamp": 1730000000000,
    }
    db.execute(
        "INSERT INTO chat VALUES ('c2','u2','Synthetic branched conversation','created-2','updated-2',NULL,0,?,0,'{}',NULL,'null',NULL,1792949159,'continuation','{}',NULL)",
        (json.dumps(payload),),
    )
    for node_id, node in nodes.items():
        db.execute(
            "INSERT INTO chat_message VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)",
            (f"c2-{node_id}", "c2", "u2", node["role"], node["parentId"], json.dumps(node["content"])),
        )
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

    def _add_admin_author(self):
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO auth VALUES ('admin','admin@example.invalid','bcrypt-placeholder',1,NULL,NULL)")
        db.execute("INSERT INTO user VALUES ('admin','Synthetic Admin','admin@example.invalid','admin','', 't','t','t','{}','{}',NULL,NULL,'{}','{}','{}')")
        db.commit()
        db.close()

    def _add_history(self, kind, resource_id, version_id, author_id):
        db = sqlite3.connect(self.source)
        if kind == "tool":
            owner_column = "tool_id"
            snapshot = json.dumps({
                "name": "Synthetic private tool",
                "content": marker_tool_source(self.root / "tool-executed-marker"),
                "meta": {"description": "synthetic", "manifest": {}},
            })
        else:
            owner_column = "model_id"
            snapshot = json.dumps({
                "name": "Synthetic private model",
                "base_model_id": "portable_pipe",
                "params": {},
                "meta": {},
            })
        db.execute(f'UPDATE "{kind}" SET version_id=? WHERE id=?', (version_id, resource_id))
        db.execute(
            f'INSERT INTO "{kind}_history" VALUES (?,?,?,?,?,?,?)',
            (version_id, resource_id, None, snapshot, author_id, "Synthetic authored revision", 10),
        )
        db.commit()
        db.close()

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
        db.execute("INSERT INTO config(key,value) VALUES ('x','{}')")
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

    def test_opt_in_private_chat_metadata_preserves_branches_authors_and_read_marker(self):
        add_second_private_chat(self.source)
        source_hash, template_hash = digest(self.source), digest(self.template)
        default_report = preview(self.source, self.uploads, self.template)
        self.assertIn('unsupported_chat_field_last_read_at', {item['code'] for item in default_report['blockers']})
        opt_in_report = preview(self.source, self.uploads, self.template, include_private_chat_metadata=True)
        self.assertNotIn('unsupported_chat_field_last_read_at', {item['code'] for item in opt_in_report['blockers']})
        self.assertEqual(opt_in_report['private_chat_metadata']['converted_chat_count'], 2)
        self.assertTrue(opt_in_report['cutover_eligible'])
        target = self.root / 'private-chat-metadata-target'
        report = convert(self.source, self.uploads, self.template, target, include_private_chat_metadata=True)
        self.assertEqual(report['private_chat_metadata']['converted_chat_count'], 2)
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))
        self.assertEqual(digest(Path(report['archive']['path']) / 'source.sqlite'), source_hash)
        db = sqlite3.connect(target / 'webui.db')
        rows = {row[0]: (json.loads(row[1]), json.loads(row[2])) for row in db.execute('SELECT id,chat,meta FROM chat')}
        db.close()
        self.assertEqual(set(rows), {'c1', 'c2'})
        first_graph, first_meta = rows['c1']
        self.assertEqual(first_graph['history']['currentId'], 'b')
        self.assertEqual(set(first_graph['history']['messages']), {'q', 'a', 'b'})
        self.assertEqual(first_graph['history']['messages']['q']['user_id'], 'u1')
        self.assertNotIn('user_id', first_graph['history']['messages']['a'])
        self.assertEqual(first_meta['_mdk_private_saved_work'], {'version': 1, 'last_read_at': None})
        second_graph, second_meta = rows['c2']
        self.assertEqual(second_graph['history']['currentId'], 'continuation')
        self.assertEqual(second_graph['history']['messages']['answer-b']['content'], 'Alternative venue')
        self.assertEqual(second_graph['history']['messages']['continuation']['content'], 'Useful continuation')
        self.assertEqual(second_meta['_mdk_private_saved_work'], {'version': 1, 'last_read_at': 1792949159})
        for node_id, node in second_graph['history']['messages'].items():
            if node['role'] == 'user':
                self.assertEqual(node['user_id'], 'u2')
                self.assertEqual(node['user'], {'id': 'u2', 'name': 'Synthetic Second User'})
            else:
                self.assertEqual(node['user_id'], 'u2')
                self.assertNotIn('user', node)
        self.assertEqual(len(second_graph['messages']), 4)

    def test_opt_in_does_not_clear_unrelated_partial_cutover_blockers(self):
        add_second_private_chat(self.source)
        db = sqlite3.connect(self.source)
        db.execute("UPDATE auth SET session_stamp='retained-session' WHERE id='u1'")
        db.execute("INSERT INTO config(key,value) VALUES ('synthetic','{}')")
        db.commit()
        db.close()
        report = preview(self.source, self.uploads, self.template, include_private_chat_metadata=True)
        codes = {item['code'] for item in report['blockers']}
        self.assertFalse(report['fatal'])
        self.assertFalse(report['cutover_eligible'])
        self.assertIn('session_stamp_not_preserved', codes)
        self.assertIn('unsupported_nonempty_domain_config', codes)
        self.assertEqual(report['private_chat_metadata']['converted_chat_count'], 2)

    def test_opt_in_flag_is_exercised_by_real_cli_preview_and_convert(self):
        add_second_private_chat(self.source)
        script = Path(__file__).with_name('migrate_sqlite.py')
        common = [sys.executable, str(script), '--source-db', str(self.source), '--uploads-root', str(self.uploads), '--empty-old-target-template', str(self.template)]
        preview_result = subprocess.run(common[:2] + ['preview'] + common[2:] + ['--include-private-chat-metadata'], text=True, capture_output=True)
        self.assertEqual(preview_result.returncode, 0)  # the minimal synthetic fixture has no unrelated blockers
        preview_json = json.loads(preview_result.stdout)
        self.assertEqual(preview_json['private_chat_metadata']['converted_chat_count'], 2)
        target = self.root / 'cli-private-chat-metadata'
        convert_result = subprocess.run(common[:2] + ['convert'] + common[2:] + ['--new-target-dir', str(target), '--include-private-chat-metadata'], text=True, capture_output=True)
        self.assertEqual(convert_result.returncode, 0)
        self.assertTrue(target.is_dir())
        db = sqlite3.connect(target / 'webui.db')
        self.assertIn('_mdk_private_saved_work', json.loads(db.execute("SELECT meta FROM chat WHERE id='c1'").fetchone()[0]))
        db.close()

    def test_opt_in_private_chat_metadata_fails_closed_before_target_creation(self):
        add_second_private_chat(self.source)
        mutations = {
            'reserved_meta': ("UPDATE chat SET meta=? WHERE id='c2'", (json.dumps({'_mdk_private_saved_work': {'version': 1, 'last_read_at': 1792949159}}),)),
            'unknown_author': ("UPDATE chat_message SET user_id='unknown' WHERE id='c2-question'", ()),
            'missing_author': ("UPDATE chat_message SET user_id=NULL WHERE id='c2-question'", ()),
            'shared': ("UPDATE chat SET share_id='shared' WHERE id='c2'", ()),
            'folder': ("UPDATE chat SET folder_id='folder' WHERE id='c2'", ()),
            'malformed_read_stamp': ("UPDATE chat SET last_read_at='not-a-stamp' WHERE id='c2'", ()),
            'negative_read_stamp': ("UPDATE chat SET last_read_at=-1 WHERE id='c2'", ()),
            'unsupported_variables': ("UPDATE chat SET variables='[1]' WHERE id='c2'", ()),
            'unsupported_tasks': ("UPDATE chat SET tasks='[1]' WHERE id='c2'", ()),
        }
        for name, (statement, values) in mutations.items():
            with self.subTest(name=name):
                # Restore the pristine multi-chat source between independent refusal cases.
                self.tearDown()
                self.temp = tempfile.TemporaryDirectory()
                self.root = Path(self.temp.name)
                self.source, self.template, self.uploads = self.root/'source.db', self.root/'template.db', self.root/'uploads'
                build_source(self.source, self.uploads)
                build_template(self.template)
                add_second_private_chat(self.source)
                db = sqlite3.connect(self.source)
                db.execute(statement, values)
                db.commit()
                db.close()
                source_hash, template_hash = digest(self.source), digest(self.template)
                target = self.root / 'refused-private-chat'
                with self.assertRaises(MigrationError) as caught:
                    convert(self.source, self.uploads, self.template, target, include_private_chat_metadata=True)
                self.assertEqual(caught.exception.code, 'fatal_source_input')
                self.assertFalse(target.exists())
                self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

    def test_opt_in_refuses_json_author_contradiction_before_target(self):
        add_second_private_chat(self.source)
        db = sqlite3.connect(self.source)
        raw = db.execute("SELECT chat FROM chat WHERE id='c2'").fetchone()[0]
        payload = json.loads(raw)
        payload['history']['messages']['question']['user_id'] = 'u1'
        db.execute("UPDATE chat SET chat=? WHERE id='c2'", (json.dumps(payload),))
        db.commit()
        db.close()
        source_hash, template_hash = digest(self.source), digest(self.template)
        target = self.root / 'refused-json-author'
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target, include_private_chat_metadata=True)
        self.assertEqual(caught.exception.code, 'fatal_source_input')
        codes = {item['code'] for item in caught.exception.report['blockers']}
        self.assertIn('private_chat_metadata_json_author_mismatch', codes)
        self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

    def test_opt_in_malformed_graph_keeps_structured_fatal_refusal(self):
        db = sqlite3.connect(self.source)
        raw = db.execute("SELECT chat FROM chat WHERE id='c1'").fetchone()[0]
        payload = json.loads(raw)
        payload['history']['messages']['a'] = None
        db.execute("UPDATE chat SET chat=? WHERE id='c1'", (json.dumps(payload),))
        db.commit()
        db.close()
        source_hash, template_hash = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template, include_private_chat_metadata=True)
        codes = {item['code'] for item in report['blockers']}
        self.assertTrue(report['fatal'])
        self.assertIn('inconsistent_chat_graph', codes)
        target = self.root / 'refused-malformed-opt-in'
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target, include_private_chat_metadata=True)
        self.assertEqual(caught.exception.code, 'fatal_source_input')
        self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))
        script = Path(__file__).with_name('migrate_sqlite.py')
        result = subprocess.run([
            sys.executable, str(script), 'convert', '--source-db', str(self.source),
            '--uploads-root', str(self.uploads), '--empty-old-target-template', str(self.template),
            '--new-target-dir', str(target), '--include-private-chat-metadata',
        ], text=True, capture_output=True)
        self.assertEqual(result.returncode, 3)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(json.loads(result.stdout)['error_code'], 'fatal_source_input')
        self.assertFalse(target.exists())

    def test_opt_in_missing_user_profile_schema_keeps_structured_refusal(self):
        db = sqlite3.connect(self.source)
        db.execute('DROP TABLE "user"')
        db.commit()
        db.close()
        source_hash, template_hash = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template, include_private_chat_metadata=True)
        self.assertTrue(report['fatal'])
        codes = {item['code'] for item in report['blockers']}
        self.assertIn('source_schema_mismatch_user', codes)
        target = self.root / 'refused-missing-user-schema'
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target, include_private_chat_metadata=True)
        self.assertEqual(caught.exception.code, 'fatal_source_input')
        self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

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
        self.assertEqual(db.execute("SELECT meta FROM chat WHERE id='c1'").fetchone()[0], '{}')
        self.assertNotIn('user_id', chat_json['history']['messages']['q'])
        self.assertEqual(chat_json['history']['currentId'], 'b')
        self.assertEqual(set(chat_json['history']['messages']), {'q', 'a', 'b'})
        self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone(), ('old-version',))
        self.assertEqual(db.execute('SELECT id,content FROM function').fetchone(), ('fn1','def pipe(): return 1'))
        marker_source = marker_tool_source(self.root / 'tool-executed-marker')
        self.assertEqual(db.execute('SELECT id,user_id,name,content,access_control FROM tool').fetchone(), ('tool-private','u1','Synthetic private tool',marker_source,'{}'))
        self.assertEqual(db.execute('SELECT id,user_id,base_model_id,name,access_control FROM model').fetchone(), ('model-private','u1','portable_pipe','Synthetic private model','{}'))
        file_row = db.execute('SELECT path FROM file').fetchone()[0]
        self.assertEqual(file_row, '/app/backend/data/uploads/f1_synthetic.html')
        self.assertEqual((target / 'uploads' / 'f1_synthetic.html').read_bytes(), (self.uploads / 'f1_synthetic.html').read_bytes())
        db.close()
        self.assertEqual(json.loads((target / 'report.json').read_text())['target_dir'], str(target.resolve()))

    def test_active_tool_model_acl_mapping_and_inert_source_archive(self):
        content = marker_tool_source(self.root / 'tool-executed-marker')
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO tool VALUES ('tool-shared','u1','Synthetic shared tool',?, '[{\"name\":\"run\"}]', '{\"description\":\"shared\",\"manifest\":{}}', NULL, 12, 11, NULL)", (content,))
        db.execute("INSERT INTO model VALUES ('model-public','u1','portable_pipe','Synthetic public model','{}','{}',12,11,1,NULL)")
        db.execute("INSERT INTO access_grant VALUES ('g-tool','tool','tool-shared','user','u1','read',12)")
        db.execute("INSERT INTO access_grant VALUES ('g-model','model','model-public','user','*','read',12)")
        db.commit(); db.close()
        before = digest(self.source)
        report = convert(self.source, self.uploads, self.template, self.root/'objects')
        self.assertFalse(report['cutover_eligible'])
        self.assertIn('access_grant_identity_and_time_not_preserved', {item['code'] for item in report['blockers']})
        db = sqlite3.connect(self.root/'objects'/'webui.db')
        self.assertEqual(db.execute("SELECT content,access_control FROM tool WHERE id='tool-shared'").fetchone(), (content, '{"read":{"user_ids":["u1"]}}'))
        self.assertEqual(db.execute("SELECT access_control FROM model WHERE id='model-public'").fetchone(), (None,))
        db.close()
        self.assertEqual(digest(self.source), before)
        self.assertEqual(digest(Path(report['archive']['path'])/'source.sqlite'), before)
        self.assertFalse((self.root/'tool-executed-marker').exists())

    def test_flat_group_membership_and_mixed_read_grant_are_prepared_without_losing_source(self):
        add_group_import_schema(self.source, self.template)
        insert_flat_group(self.source)
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO access_grant VALUES ('group-tool','tool','tool-private','group','g1','read',12)")
        db.execute("INSERT INTO access_grant VALUES ('user-tool','tool','tool-private','user','u1','read',13)")
        db.commit(); db.close()
        add_user(self.source, "u10")
        source_before, template_before = digest(self.source), digest(self.template)

        report = convert(self.source, self.uploads, self.template, self.root / "group-target")
        self.assertFalse(report["cutover_eligible"])
        blockers = {item["code"] for item in report["blockers"]}
        self.assertIn("group_membership_identity_and_time_not_preserved", blockers)
        self.assertIn("access_grant_identity_and_time_not_preserved", blockers)
        db = sqlite3.connect(self.root / "group-target" / "webui.db")
        group = db.execute(
            'SELECT id,user_id,name,description,data,meta,permissions,user_ids,created_at,updated_at FROM "group"'
        ).fetchone()
        self.assertEqual(group, (
            "g1", "u1", "Synthetic read group", "Flat read group fixture",
            '{"config":{"share":"members"}}', None, "{}", '["u1"]', 100, 101,
        ))
        acl = db.execute("SELECT access_control FROM tool WHERE id='tool-private'").fetchone()[0]
        self.assertEqual(acl, '{"read":{"group_ids":["g1"],"user_ids":["u1"]}}')
        user_ids_json = group[7]
        self.assertEqual(json.loads(user_ids_json), ["u1"])
        for candidate, expected in (("u1", True), ("u10", False)):
            matched = bool(db.execute("SELECT ? LIKE ?", (user_ids_json, f'%"{candidate}"%')).fetchone()[0])
            self.assertEqual(matched, expected)
        db.close()
        self.assertEqual(digest(self.source), source_before)
        self.assertEqual(digest(self.template), template_before)
        self.assertEqual(digest(Path(report["archive"]["path"]) / "source.sqlite"), source_before)
        self.assertEqual(report["imported_counts"]["group"], 1)
        self.assertEqual(report["imported_counts"]["group_member"], 0)
        self.assertFalse((self.root / "tool-executed-marker").exists())

    def test_nondefault_dangling_duplicate_and_invalid_group_inputs_refuse_before_target(self):
        cases = [
            ("parent", "unsupported_group_hierarchy"),
            ("permissions", "unsupported_group_permissions"),
            ("data", "unsupported_group_data_profile"),
            ("meta", "unsupported_group_meta_profile"),
            ("meta_json_null", "unsupported_group_meta_profile"),
            ("timestamp", "invalid_group_timestamp"),
            ("owner", "group_owner_missing"),
            ("unknown_member", "group_member_user_missing"),
            ("dangling_member", "dangling_group_membership"),
            ("duplicate_member", "duplicate_group_membership"),
            ("member_timestamp", "invalid_group_member_timestamp"),
            ("schema", "source_schema_mismatch_group"),
            ("member_schema", "source_schema_mismatch_group_member"),
            ("group_wildcard", "unsupported_access_grant_semantics"),
            ("group_write", "unsupported_access_grant_semantics"),
            ("dangling_group_grant", "unknown_or_unsupported_access_grant_group"),
            ("target_schema", "old_template_group_table_missing"),
            ("target_extra_schema", "old_template_group_schema_mismatch"),
            ("missing_member_table", "group_membership_schema_missing"),
        ]
        for case, expected in cases:
            with self.subTest(case=case):
                self.tearDown(); self.setUp()
                add_group_import_schema(self.source, self.template, target_group=case != "target_schema")
                insert_flat_group(self.source)
                db = sqlite3.connect(self.source)
                if case == "parent":
                    db.execute('UPDATE "group" SET parent_group_id="parent"')
                elif case == "permissions":
                    db.execute('UPDATE "group" SET permissions=?', ('{"workspace":{"tools":true}}',))
                elif case == "data":
                    db.execute('UPDATE "group" SET data=?', ('{"config":{"share":"everyone"}}',))
                elif case == "meta":
                    db.execute('UPDATE "group" SET meta=?', ('{"unmeasured":"data"}',))
                elif case == "meta_json_null":
                    db.execute('UPDATE "group" SET meta=?', ('null',))
                elif case == "timestamp":
                    db.execute('UPDATE "group" SET created_at="not-a-time"')
                elif case == "owner":
                    db.execute('UPDATE "group" SET user_id="missing-owner"')
                elif case == "unknown_member":
                    db.execute('UPDATE group_member SET user_id="missing-user"')
                elif case == "dangling_member":
                    db.execute('UPDATE group_member SET group_id="missing-group"')
                elif case == "duplicate_member":
                    db.execute("INSERT INTO group_member VALUES ('gm2','g1','u1',102,103)")
                elif case == "member_timestamp":
                    db.execute('UPDATE group_member SET updated_at="not-a-time"')
                elif case == "schema":
                    db.execute('ALTER TABLE "group" ADD COLUMN unexpected TEXT')
                elif case == "member_schema":
                    db.execute('ALTER TABLE group_member ADD COLUMN unexpected TEXT')
                elif case == "group_wildcard":
                    db.execute("INSERT INTO access_grant VALUES ('g','tool','tool-private','group','*','read',12)")
                elif case == "group_write":
                    db.execute("INSERT INTO access_grant VALUES ('g','tool','tool-private','group','g1','write',12)")
                elif case == "dangling_group_grant":
                    db.execute("INSERT INTO access_grant VALUES ('g','tool','tool-private','group','missing-group','read',12)")
                elif case == "target_extra_schema":
                    target_db = sqlite3.connect(self.template)
                    target_db.execute('ALTER TABLE "group" ADD COLUMN unsupported TEXT')
                    target_db.commit(); target_db.close()
                elif case == "missing_member_table":
                    db.execute('DROP TABLE group_member')
                db.commit(); db.close()
                source_before, template_before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report["fatal"])
                self.assertIn(expected, {item["code"] for item in report["blockers"]})
                target = self.root / "refused-group-target"
                with self.assertRaises(MigrationError):
                    convert(self.source, self.uploads, self.template, target)
                self.assertFalse(target.exists())
                self.assertEqual((digest(self.source), digest(self.template)), (source_before, template_before))

    def test_group_membership_identity_shapes_survive_import_for_exact_runtime_matching(self):
        add_group_import_schema(self.source, self.template)
        insert_flat_group(self.source)
        unusual_ids = ["u_", "u%", 'u"1', "Upper", "upper"]
        for index, user_id in enumerate(unusual_ids):
            add_user(self.source, user_id)
            db = sqlite3.connect(self.source)
            if index == 0:
                db.execute('UPDATE group_member SET user_id=?', (user_id,))
            else:
                db.execute("INSERT INTO group_member VALUES (?,?,?,?,?)", (f"gm{index + 1}", "g1", user_id, 100 + index, 101 + index))
            db.commit(); db.close()
        report = convert(self.source, self.uploads, self.template, self.root / "unusual-membership-target")
        self.assertFalse(report["cutover_eligible"])
        db = sqlite3.connect(self.root / "unusual-membership-target" / "webui.db")
        stored = json.loads(db.execute('SELECT user_ids FROM "group" WHERE id="g1"').fetchone()[0])
        db.close()
        self.assertEqual(stored, unusual_ids)
        self.assertEqual(report["imported_counts"]["group_member"], 0)

    def test_group_and_membership_resource_caps_refuse_without_target(self):
        for mode in ("groups", "memberships"):
            with self.subTest(mode=mode):
                self.tearDown(); self.setUp()
                add_group_import_schema(self.source, self.template)
                db = sqlite3.connect(self.source)
                if mode == "groups":
                    db.executemany(
                        'INSERT INTO "group" VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                        ((f"g{i}", "u1", "Synthetic group", "Description", '{"config":{"share":"members"}}', None, "{}", 1, 1) for i in range(1001)),
                    )
                else:
                    db.executemany(
                        "INSERT INTO group_member VALUES (?,?,?,?,?)",
                        ((f"gm{i}", "g1", "u1", 1, 1) for i in range(10001)),
                    )
                db.commit(); db.close()
                source_before, template_before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report["fatal"])
                self.assertIn("group_import_resource_cap_exceeded", {item["code"] for item in report["blockers"]})
                target = self.root / "over-cap-target"
                with self.assertRaises(MigrationError):
                    convert(self.source, self.uploads, self.template, target)
                self.assertFalse(target.exists())
                self.assertEqual((digest(self.source), digest(self.template)), (source_before, template_before))

    def test_object_grants_and_version_links_fail_closed(self):
        bad_inputs = [
            (["INSERT INTO access_grant VALUES ('g','tool','tool-private','group','*','read',12)"], 'unsupported_access_grant_semantics'),
            (["INSERT INTO access_grant VALUES ('g','tool','tool-private','user','u1','write',12)"], 'unsupported_access_grant_semantics'),
            (["INSERT INTO access_grant VALUES ('g','tool','missing','user','u1','read',12)"], 'dangling_or_unknown_access_grant'),
            (["INSERT INTO access_grant VALUES ('g','tool','tool-private','user','missing-user','read',12)"], 'unknown_access_grant_user'),
            (["INSERT INTO access_grant VALUES ('g1','tool','tool-private','user','u1','read',12)", "INSERT INTO access_grant VALUES ('g2','tool','tool-private','user','u1','read',13)"], 'duplicate_access_grant'),
            (["INSERT INTO access_grant VALUES ('g1','tool','tool-private','user','*','read',12)", "INSERT INTO access_grant VALUES ('g2','tool','tool-private','user','u1','read',13)"], 'public_and_targeted_grants_conflict'),
            (["UPDATE tool SET version_id='absent' WHERE id='tool-private'"], 'invalid_current_version_link_tool'),
            (["UPDATE tool SET version_id='bad-snapshot' WHERE id='tool-private'", "INSERT INTO tool_history VALUES ('bad-snapshot','tool-private',NULL,'{}','u1',NULL,10)"], 'invalid_history_snapshot_tool'),
            (["UPDATE model SET version_id='missing-table-version' WHERE id='model-private'", "DROP TABLE model_history"], 'missing_history_table_for_version_model'),
            (["UPDATE model SET version_id='missing-field-version' WHERE id='model-private'", "INSERT INTO model_history VALUES ('missing-field-version','model-private',NULL,'{\"name\":\"x\",\"params\":{},\"meta\":{}}','u1',NULL,10)"], 'invalid_history_snapshot_model'),
        ]
        for statements, expected in bad_inputs:
            with self.subTest(expected=expected):
                self.tearDown()
                self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
                self.source, self.template, self.uploads = self.root/'source.db', self.root/'template.db', self.root/'uploads'
                build_source(self.source, self.uploads); build_template(self.template)
                db = sqlite3.connect(self.source)
                for sql in statements:
                    db.execute(sql)
                db.commit(); db.close()
                before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report['fatal'])
                self.assertIn(expected, {item['code'] for item in report['blockers']})
                self.assertEqual((digest(self.source), digest(self.template)), before)
                target = self.root/'candidate'
                with self.assertRaises(MigrationError):
                    convert(self.source, self.uploads, self.template, target)
                self.assertFalse(target.exists())

    def test_valid_current_history_links_are_archived_but_block_cutover(self):
        db = sqlite3.connect(self.source)
        marker_source = marker_tool_source(self.root / 'tool-executed-marker')
        tool_snapshot = json.dumps({"name":"Synthetic private tool","content":marker_source,"meta":{"description":"synthetic","manifest":{}}})
        model_snapshot = json.dumps({"name":"Synthetic private model","base_model_id":"portable_pipe","params":{},"meta":{}})
        db.execute("UPDATE tool SET version_id='tool-version' WHERE id='tool-private'")
        db.execute("UPDATE model SET version_id='model-version' WHERE id='model-private'")
        db.execute("INSERT INTO tool_history VALUES ('tool-version','tool-private',NULL,?,'u1',NULL,10)", (tool_snapshot,))
        db.execute("INSERT INTO model_history VALUES ('model-version','model-private',NULL,?,'u1',NULL,10)", (model_snapshot,))
        db.commit(); db.close()
        source_hash = digest(self.source)
        report = convert(self.source, self.uploads, self.template, self.root/'history')
        codes = {item['code'] for item in report['blockers']}
        self.assertIn('active_tool_history_not_preserved', codes)
        self.assertIn('active_model_history_not_preserved', codes)
        self.assertIn('active_tool_version_link_not_preserved', codes)
        self.assertIn('active_model_version_link_not_preserved', codes)
        self.assertFalse(report['cutover_eligible'])
        self.assertEqual(digest(Path(report['archive']['path'])/'source.sqlite'), source_hash)
        db = sqlite3.connect(self.root/'history'/'webui.db')
        self.assertEqual(db.execute("SELECT id,content FROM tool WHERE id='tool-private'").fetchone(), ('tool-private', marker_source))
        self.assertEqual(db.execute("SELECT id,base_model_id FROM model WHERE id='model-private'").fetchone(), ('model-private','portable_pipe'))
        db.close()

    def test_known_history_author_may_differ_from_active_resource_owner(self):
        self._add_admin_author()
        self._add_history("tool", "tool-private", "tool-admin-version", "admin")
        self._add_history("model", "model-private", "model-admin-version", "admin")
        source_hash = digest(self.source)

        report = convert(self.source, self.uploads, self.template, self.root / "authored-history")

        codes = {item["code"] for item in report["blockers"]}
        self.assertFalse(report["cutover_eligible"])
        self.assertIn("active_tool_history_not_preserved", codes)
        self.assertIn("active_model_history_not_preserved", codes)
        self.assertIn("active_tool_version_link_not_preserved", codes)
        self.assertIn("active_model_version_link_not_preserved", codes)
        self.assertNotIn("unknown_history_author_tool", codes)
        self.assertNotIn("unknown_history_author_model", codes)
        archive = Path(report["archive"]["path"]) / "source.sqlite"
        self.assertEqual(digest(archive), source_hash)
        self.assertEqual(digest(self.source), source_hash)

        archived_db = sqlite3.connect(archive)
        self.assertEqual(
            archived_db.execute("SELECT user_id FROM tool_history WHERE id='tool-admin-version'").fetchone(),
            ("admin",),
        )
        self.assertEqual(
            archived_db.execute("SELECT user_id FROM model_history WHERE id='model-admin-version'").fetchone(),
            ("admin",),
        )
        self.assertEqual(archived_db.execute("SELECT user_id FROM tool WHERE id='tool-private'").fetchone(), ("u1",))
        self.assertEqual(archived_db.execute("SELECT user_id FROM model WHERE id='model-private'").fetchone(), ("u1",))
        archived_db.close()

        target_db = sqlite3.connect(self.root / "authored-history" / "webui.db")
        self.assertEqual(target_db.execute("SELECT user_id FROM tool WHERE id='tool-private'").fetchone(), ("u1",))
        self.assertEqual(target_db.execute("SELECT user_id FROM model WHERE id='model-private'").fetchone(), ("u1",))
        self.assertNotIn("version_id", {row[1] for row in target_db.execute("PRAGMA table_info(tool)")})
        self.assertNotIn("version_id", {row[1] for row in target_db.execute("PRAGMA table_info(model)")})
        target_db.close()

    def test_unknown_or_malformed_history_author_is_fatal_before_target(self):
        cases = (
            ("tool", "missing-user", "unknown_history_author_tool"),
            ("model", "missing-user", "unknown_history_author_model"),
            ("tool", "", "invalid_history_author_tool"),
            ("model", None, "invalid_history_author_model"),
        )
        for kind, author_id, expected in cases:
            with self.subTest(kind=kind, author_id=author_id, expected=expected):
                self.tearDown()
                self.temp = tempfile.TemporaryDirectory()
                self.root = Path(self.temp.name)
                self.source, self.template, self.uploads = self.root / "source.db", self.root / "template.db", self.root / "uploads"
                build_source(self.source, self.uploads)
                build_template(self.template)
                resource_id = "tool-private" if kind == "tool" else "model-private"
                self._add_history(kind, resource_id, kind + "-author-check", author_id)
                before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report["fatal"])
                self.assertIn(expected, {item["code"] for item in report["blockers"]})
                target = self.root / "candidate"
                with self.assertRaises(MigrationError):
                    convert(self.source, self.uploads, self.template, target)
                self.assertFalse(target.exists())
                self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_dangling_history_resource_and_parent_remain_fatal(self):
        for defect, expected in (
            ("resource", "history_resource_missing_tool"),
            ("parent", "invalid_history_parent_tool"),
        ):
            with self.subTest(defect=defect):
                self.tearDown()
                self.temp = tempfile.TemporaryDirectory()
                self.root = Path(self.temp.name)
                self.source, self.template, self.uploads = self.root / "source.db", self.root / "template.db", self.root / "uploads"
                build_source(self.source, self.uploads)
                build_template(self.template)
                self._add_history("tool", "tool-private", "tool-link-check", "u1")
                db = sqlite3.connect(self.source)
                if defect == "resource":
                    db.execute("UPDATE tool_history SET tool_id='missing-tool' WHERE id='tool-link-check'")
                else:
                    db.execute("UPDATE tool_history SET parent_id='missing-parent' WHERE id='tool-link-check'")
                db.commit()
                db.close()
                before = digest(self.source), digest(self.template)
                report = preview(self.source, self.uploads, self.template)
                self.assertTrue(report["fatal"])
                self.assertIn(expected, {item["code"] for item in report["blockers"]})
                target = self.root / "candidate"
                with self.assertRaises(MigrationError):
                    convert(self.source, self.uploads, self.template, target)
                self.assertFalse(target.exists())
                self.assertEqual((digest(self.source), digest(self.template)), before)

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
            ("INSERT INTO access_grant VALUES ('grant','tool','tool-private','group','*','read',12)", 'unsupported_access_grant_semantics'),
            ("INSERT INTO \"group\" VALUES ('g')", 'source_schema_mismatch_group'),
            ("INSERT INTO group_member VALUES ('m')", 'source_schema_mismatch_group'),
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


class NamedToolConfigurationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.template = self.root / "template.db"
        self.uploads = self.root / "uploads"
        build_source(self.source, self.uploads)
        build_template(self.template)

    def tearDown(self):
        self.temp.cleanup()

    def test_public_inspector_keeps_five_value_return_with_opt_in(self):
        add_named_tool_configuration(self.source)
        ordinary = importer.inspect_source(self.source, self.uploads, self.template)
        opted_in = importer.inspect_source(
            self.source,
            self.uploads,
            self.template,
            include_named_tool_configuration=True,
        )
        self.assertEqual(len(ordinary), 5)
        self.assertEqual(len(opted_in), 5)
        self.assertEqual(opted_in[0]["named_tool_configuration"]["selected_connection_count"], 1)
        self.assertEqual(preview(
            self.source, self.uploads, self.template,
            include_named_tool_configuration=True,
        )["named_tool_configuration"]["selected_connection_count"], 1)

    def test_optional_connection_fields_remain_absent_in_imported_config(self):
        selected = named_tool_connection()
        selected.pop("headers")
        selected.pop("forward_cookies")
        add_named_tool_configuration(self.source, selected)
        report = preview(
            self.source, self.uploads, self.template,
            include_named_tool_configuration=True,
        )
        self.assertFalse(report["fatal"])
        target = self.root / "optional-fields"
        result = convert(
            self.source, self.uploads, self.template, target,
            include_named_tool_configuration=True,
        )
        self.assertFalse(result["fatal"])
        db = sqlite3.connect(target / "webui.db")
        try:
            imported = json.loads(db.execute("SELECT data FROM config").fetchone()[0])
        finally:
            db.close()
        imported_connection = imported["tool_server"]["connections"][0]
        self.assertEqual(imported_connection, selected)
        self.assertNotIn("headers", imported_connection)
        self.assertNotIn("forward_cookies", imported_connection)

    def test_bounded_alternate_named_connection_is_copied_exactly_without_fetching(self):
        selected = named_tool_connection("u1")
        selected["url"] = "http://[ABCD::1]:18081/service"
        selected["path"] = "api/openapi.json"
        selected["info"] = {
            "id": "community_tools",
            "name": "Community workshop tools",
            "description": "Synthetic tools — demonstration",
        }
        self.assertTrue(importer._valid_named_tool_base_url("http://[ABCD::1]:18081/service"))
        add_named_tool_configuration(self.source, selected)
        before = digest(self.source), digest(self.template)
        report = preview(
            self.source, self.uploads, self.template,
            include_named_tool_configuration=True,
        )
        self.assertFalse(report["fatal"])
        self.assertFalse(report["cutover_eligible"])
        target = self.root / "alternate-named-config"
        result = convert(
            self.source, self.uploads, self.template, target,
            include_named_tool_configuration=True,
        )
        self.assertFalse(result["fatal"])
        db = sqlite3.connect(target / "webui.db")
        try:
            imported = json.loads(db.execute("SELECT data FROM config").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(imported["tool_server"]["connections"][0], selected)
        self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_duplicate_selected_user_identity_with_different_roles_refuses(self):
        add_named_tool_configuration(self.source)
        db = sqlite3.connect(self.source)
        duplicate = list(db.execute('SELECT * FROM "user" WHERE id=?', ("u1",)).fetchone())
        duplicate[3] = "admin"
        db.execute('INSERT INTO "user" VALUES (' + ",".join("?" for _ in duplicate) + ")", duplicate)
        db.commit()
        db.close()
        source_hash, template_hash = digest(self.source), digest(self.template)
        target = self.root / "duplicate-selected-user"
        with self.assertRaises(MigrationError) as caught:
            convert(
                self.source, self.uploads, self.template, target,
                include_named_tool_configuration=True,
            )
        codes = {row["code"] for row in caught.exception.report["blockers"]}
        self.assertIn("named_tool_configuration_grant_identity_invalid", codes)
        self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

    def test_opt_in_preview_and_convert_preserve_named_identity_defaults_and_archive(self):
        add_named_tool_configuration(self.source, other_rows=377)
        before = digest(self.source), digest(self.template)
        report = preview(
            self.source,
            self.uploads,
            self.template,
            include_named_tool_configuration=True,
        )
        self.assertFalse(report["fatal"])
        self.assertFalse(report["cutover_eligible"])
        blockers = {row["code"]: row["count"] for row in report["blockers"]}
        self.assertEqual(blockers["unsupported_nonempty_domain_config"], 377)
        self.assertEqual(blockers["named_tool_configuration_prototype"], 1)
        self.assertEqual(
            report["named_tool_configuration"],
            {
                "opt_in": True,
                "selected_connection_count": 1,
                "old_defaults_verified": True,
                "defaults_source_sha256": importer.OLD_CONFIG_SOURCE_SHA256,
                "defaults_literal_sha256": importer.OLD_CONFIG_DEFAULTS_SHA256,
                "remaining_config_rows": 377,
            },
        )
        encoded_report = json.dumps(report)
        self.assertNotIn("synthetic_openapi", encoded_report)
        self.assertNotIn("Synthetic OpenAPI tool fixture", encoded_report)
        self.assertNotIn('"principal_id"', encoded_report)
        self.assertEqual((digest(self.source), digest(self.template)), before)

        target = self.root / "candidate"
        converted = convert(
            self.source,
            self.uploads,
            self.template,
            target,
            include_named_tool_configuration=True,
        )
        self.assertFalse(converted["cutover_eligible"])
        self.assertEqual(converted["imported_counts"]["config"], 1)
        self.assertEqual(converted["target_readback_counts"]["config"], 1)
        self.assertEqual((digest(self.source), digest(self.template)), before)
        target_db = sqlite3.connect(target / "webui.db")
        try:
            config_row = target_db.execute("SELECT id,data,version FROM config").fetchone()
            self.assertEqual((config_row[0], config_row[2]), (1, 0))
            saved = json.loads(config_row[1])
            defaults, source_hash, literal_hash = importer._load_pinned_old_defaults()
            self.assertEqual(source_hash, importer.OLD_CONFIG_SOURCE_SHA256)
            self.assertEqual(literal_hash, importer.OLD_CONFIG_DEFAULTS_SHA256)
            expected = json.loads(json.dumps(defaults, sort_keys=True, separators=(",", ":")))
            expected["tool_server"] = {"connections": [named_tool_connection()]}
            self.assertEqual(saved, expected)
            self.assertEqual(saved["version"], defaults["version"])
            self.assertEqual(saved["ui"], defaults["ui"])
            connection = saved["tool_server"]["connections"][0]
            self.assertEqual(connection["info"]["id"], "synthetic_openapi")
            self.assertEqual(connection["config"]["access_grants"][0]["principal_id"], "u1")
        finally:
            target_db.close()
        archive = Path(converted["archive"]["path"])
        self.assertEqual(digest(archive / "source.sqlite"), before[0])
        self.assertNotEqual(archive, target)

    def test_default_off_does_not_materialize_selected_config(self):
        add_named_tool_configuration(self.source)
        before = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template)
        self.assertNotIn("named_tool_configuration", report)
        self.assertIn(
            "unsupported_nonempty_domain_config",
            {row["code"] for row in report["blockers"]},
        )
        target = self.root / "default-off"
        result = convert(self.source, self.uploads, self.template, target)
        self.assertNotIn("config", result["imported_counts"])
        target_db = sqlite3.connect(target / "webui.db")
        try:
            self.assertEqual(target_db.execute("SELECT COUNT(*) FROM config").fetchone()[0], 0)
        finally:
            target_db.close()
        self.assertEqual((digest(self.source), digest(self.template)), before)

    def test_cli_opt_in_preview_and_convert_return_blocked_partial_receipts(self):
        add_named_tool_configuration(self.source, other_rows=377)
        source_hash, template_hash = digest(self.source), digest(self.template)
        script = Path(__file__).with_name("migrate_sqlite.py")
        common = [
            sys.executable,
            str(script),
            "--source-db",
            str(self.source),
            "--uploads-root",
            str(self.uploads),
            "--empty-old-target-template",
            str(self.template),
            "--include-named-tool-configuration",
        ]
        preview_result = subprocess.run(
            common[:2] + ["preview"] + common[2:], text=True, capture_output=True
        )
        self.assertEqual(preview_result.returncode, 3)
        preview_report = json.loads(preview_result.stdout)
        self.assertEqual(preview_report["named_tool_configuration"]["selected_connection_count"], 1)
        self.assertFalse(preview_report["cutover_eligible"])

        target = self.root / "cli-target"
        convert_result = subprocess.run(
            common[:2]
            + ["convert"]
            + common[2:]
            + ["--new-target-dir", str(target)],
            text=True,
            capture_output=True,
        )
        self.assertEqual(convert_result.returncode, 3)
        converted = json.loads(convert_result.stdout)
        self.assertFalse(converted["cutover_eligible"])
        target_db = sqlite3.connect(target / "webui.db")
        try:
            imported = json.loads(target_db.execute("SELECT data FROM config").fetchone()[0])
            self.assertEqual(imported["tool_server"]["connections"][0]["info"]["id"], "synthetic_openapi")
        finally:
            target_db.close()
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

    def _assert_opt_in_refused(self, connection_value, expected_code, *, duplicate=False, admin=False, bad_source_schema=False, bad_target_schema=False, missing=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source, template, uploads = root / "source.db", root / "template.db", root / "uploads"
        build_source(source, uploads)
        build_template(template)
        if admin:
            db = sqlite3.connect(source)
            db.execute("UPDATE user SET role='admin' WHERE id='u1'")
            db.commit()
            db.close()
        if bad_source_schema:
            db = sqlite3.connect(source)
            db.execute("ALTER TABLE config ADD COLUMN unsupported TEXT")
            db.commit()
            db.close()
        if bad_target_schema:
            db = sqlite3.connect(template)
            db.execute("ALTER TABLE config ADD COLUMN unsupported TEXT")
            db.commit()
            db.close()
        if not missing:
            add_named_tool_configuration(source, connection_value)
            if duplicate:
                db = sqlite3.connect(source)
                raw = connection_value if isinstance(connection_value, str) else json.dumps([connection_value])
                db.execute(
                    "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                    ("tool_server.connections", raw),
                )
                db.commit()
                db.close()
        source_hash, template_hash = digest(source), digest(template)
        target = root / "target"
        with self.assertRaises(MigrationError) as caught:
            convert(
                source,
                uploads,
                template,
                target,
                include_named_tool_configuration=True,
            )
        self.assertEqual(caught.exception.code, "fatal_source_input")
        self.assertTrue(caught.exception.report["fatal"])
        codes = {row["code"] for row in caught.exception.report["blockers"]}
        self.assertIn(expected_code, codes)
        self.assertFalse(target.exists())
        self.assertEqual((digest(source), digest(template)), (source_hash, template_hash))
        refusal_text = str(caught.exception) + json.dumps(caught.exception.report)
        self.assertNotIn("Synthetic OpenAPI tool fixture", refusal_text)
        self.assertNotIn('"principal_id"', refusal_text)
        self.assertNotIn("synthetic-secret-marker", refusal_text)

    def test_unsupported_connection_and_grant_shapes_refuse_before_target(self):
        cases = []
        unknown = named_tool_connection(); unknown["unexpected"] = "synthetic-marker"
        cases.append((unknown, "named_tool_configuration_unsupported_shape", False))
        credential = named_tool_connection(); credential["key"] = "synthetic-secret-marker"
        cases.append((credential, "named_tool_configuration_unsupported_shape", False))
        bad_url = named_tool_connection(); bad_url["url"] = "https://example.invalid/spec/../openapi.json"
        cases.append((bad_url, "named_tool_configuration_unsupported_shape", False))
        group = named_tool_connection(); group["config"]["access_grants"][0]["principal_type"] = "group"
        cases.append((group, "named_tool_configuration_unsupported_grant", False))
        write = named_tool_connection(); write["config"]["access_grants"][0]["permission"] = "write"
        cases.append((write, "named_tool_configuration_unsupported_grant", False))
        public = named_tool_connection("*")
        cases.append((public, "named_tool_configuration_grant_identity_invalid", False))
        dangling = named_tool_connection("missing-user")
        cases.append((dangling, "named_tool_configuration_grant_identity_invalid", False))
        blank = named_tool_connection("")
        cases.append((blank, "named_tool_configuration_grant_identity_invalid", False))
        multiple = named_tool_connection(); multiple["config"]["access_grants"].append(dict(multiple["config"]["access_grants"][0]))
        cases.append((multiple, "named_tool_configuration_unsupported_grant", False))
        multiple_connections = [named_tool_connection(), named_tool_connection()]
        cases.append((multiple_connections, "named_tool_configuration_unsupported_shape", False))
        admin = named_tool_connection()
        cases.append((admin, "named_tool_configuration_grant_identity_invalid", True))
        for bad_url in (
            "http://",
            "http://localhost:bad",
            "http://localhost:0",
            "http://localhost:65536",
            "https://user:pass@example.invalid",
            "https://example.invalid?token=x",
            "https://example.invalid#fragment",
            "http://[::1]junk",
            "http://[127.0.0.1]",
            "http://[::1]:18081/service/../other",
            "http://[::1]:18081/%2e%2e/other",
            " https://example.invalid",
        ):
            malformed_url = named_tool_connection(); malformed_url["url"] = bad_url
            cases.append((malformed_url, "named_tool_configuration_unsupported_shape", False))
        for bad_path in (
            "",
            "/openapi.json",
            "../openapi.json",
            "api/../openapi.json",
            "api\\openapi.json",
            "https://example.invalid/openapi.json",
            "openapi.json?x=1",
            "api/%2e%2e/openapi.json",
        ):
            malformed_path = named_tool_connection(); malformed_path["path"] = bad_path
            cases.append((malformed_path, "named_tool_configuration_unsupported_shape", False))
        for bad_id in ("bad/id", "123", "x" * 129):
            malformed_id = named_tool_connection(); malformed_id["info"]["id"] = bad_id
            cases.append((malformed_id, "named_tool_configuration_unsupported_shape", False))
        for key, bad_value in (
            ("name", "   "),
            ("name", "name\nwith-control"),
            ("name", 42),
            ("name", "n" * (importer.MAX_NAMED_TOOL_NAME_CHARS + 1)),
            ("description", 42),
            ("description", "description\u0085with-control"),
            ("description", "d" * (importer.MAX_NAMED_TOOL_DESCRIPTION_CHARS + 1)),
        ):
            malformed_metadata = named_tool_connection()
            malformed_metadata["info"][key] = bad_value
            cases.append((malformed_metadata, "named_tool_configuration_unsupported_shape", False))
        for value, expected, is_admin in cases:
            with self.subTest(code=expected, value_shape=type(value).__name__, admin=is_admin):
                self._assert_opt_in_refused(value, expected, admin=is_admin)
        empty_headers = named_tool_connection(); empty_headers["headers"] = {}
        add_named_tool_configuration(self.source, empty_headers)
        report = preview(
            self.source, self.uploads, self.template,
            include_named_tool_configuration=True,
        )
        self.assertEqual(report["named_tool_configuration"]["selected_connection_count"], 1)
        self.assertFalse(report["cutover_eligible"])

    def test_duplicate_oversized_malformed_or_missing_config_refuses(self):
        self._assert_opt_in_refused(named_tool_connection(), "named_tool_configuration_selection_invalid", duplicate=True)
        self._assert_opt_in_refused(" " * (importer.MAX_NAMED_TOOL_CONFIG_BYTES + 1), "named_tool_configuration_selection_invalid")
        self._assert_opt_in_refused('{"x":1,"x":2}', "named_tool_configuration_selection_invalid")
        self._assert_opt_in_refused(None, "named_tool_configuration_selection_invalid", missing=True)

    def test_source_target_schema_and_default_hash_mismatches_refuse(self):
        connection = named_tool_connection()
        self._assert_opt_in_refused(connection, "named_tool_configuration_source_schema_mismatch", bad_source_schema=True)
        self._assert_opt_in_refused(connection, "old_template_named_config_schema_mismatch", bad_target_schema=True)
        add_named_tool_configuration(self.source)
        source_hash, template_hash = digest(self.source), digest(self.template)
        target = self.root / "default-hash-refusal"
        for attribute in ("OLD_CONFIG_SOURCE_SHA256", "OLD_CONFIG_DEFAULTS_SHA256"):
            with self.subTest(hash=attribute), mock.patch.object(importer, attribute, "0" * 64):
                with self.assertRaises(MigrationError) as caught:
                    convert(
                        self.source,
                        self.uploads,
                        self.template,
                        target,
                        include_named_tool_configuration=True,
                    )
                self.assertIn(
                    "named_tool_configuration_defaults_unavailable",
                    {row["code"] for row in caught.exception.report["blockers"]},
                )
                self.assertFalse(target.exists())
        self.assertEqual((digest(self.source), digest(self.template)), (source_hash, template_hash))

    def test_default_loader_is_pure_and_does_not_import_runtime_config(self):
        import builtins

        original_import = builtins.__import__

        def deny_runtime_config(name, *args, **kwargs):
            if name == "open_webui.config":
                raise AssertionError("runtime config module import is forbidden")
            return original_import(name, *args, **kwargs)

        connection = named_tool_connection()
        add_named_tool_configuration(self.source, connection)
        with mock.patch("builtins.__import__", side_effect=deny_runtime_config):
            report = preview(
                self.source,
                self.uploads,
                self.template,
                include_named_tool_configuration=True,
            )
        self.assertTrue(report["named_tool_configuration"]["old_defaults_verified"])


if __name__ == '__main__':
    unittest.main(verbosity=2)
