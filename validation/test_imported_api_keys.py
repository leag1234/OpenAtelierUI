#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Named SQLite controls for the opt-in legacy API-key import slice."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from migrate_sqlite import MigrationError, convert, preview
from test_migrate_sqlite import add_user, build_source, build_template, digest


SYNTHETIC_KEY = "sk-" + "a" * 32
POLICY = {
    "auth.enable_api_keys": True,
    "user.permissions": {"features": {"api_keys": True}},
    "auth.api_key.endpoint_restrictions": False,
    "auth.api_key.allowed_endpoints": "",
}


def add_api_key_source(
    source: Path,
    *,
    policy: dict | None = None,
    owner: str = "u1",
    key: str = SYNTHETIC_KEY,
    data=None,
    expires_at=None,
    table_columns: str = "id TEXT,user_id TEXT,key TEXT,data TEXT,expires_at INTEGER,last_used_at INTEGER,created_at INTEGER,updated_at INTEGER",
) -> None:
    db = sqlite3.connect(source)
    db.execute(f"CREATE TABLE api_key({table_columns})")
    db.execute(
        "INSERT INTO api_key(id,user_id,key,data,expires_at,last_used_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("synthetic-key-row", owner, key, data, expires_at, None, 1700000000, 1700000001),
    )
    for config_key, value in (policy or POLICY).items():
        db.execute(
            "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
            (config_key, json.dumps(value, separators=(",", ":"))),
        )
    db.commit()
    db.close()


class ImportedApiKeyTests(unittest.TestCase):
    def setUp(self):
        self._new_fixture()

    def _new_fixture(self):
        if hasattr(self, "temp"):
            self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.template = self.root / "template.db"
        self.uploads = self.root / "uploads"
        build_source(self.source, self.uploads)
        build_template(self.template)

    def tearDown(self):
        self.temp.cleanup()

    def _assert_refused_before_target(self, *, expected_code: str = "fatal_source_input"):
        source_hash = digest(self.source)
        template_hash = digest(self.template)
        target = self.root / "refused-target"
        with self.assertRaises(MigrationError) as caught:
            convert(
                self.source,
                self.uploads,
                self.template,
                target,
                include_api_keys=True,
            )
        self.assertEqual(caught.exception.code, expected_code)
        self.assertFalse(target.exists())
        self.assertEqual(digest(self.source), source_hash)
        self.assertEqual(digest(self.template), template_hash)
        text = json.dumps(caught.exception.report, sort_keys=True)
        self.assertNotIn(SYNTHETIC_KEY, text)

    def test_default_off_keeps_original_fatal_domain_refusal(self):
        add_api_key_source(self.source)
        report = preview(self.source, self.uploads, self.template)
        blocker = next(
            item for item in report["blockers"]
            if item["code"] == "unsupported_nonempty_domain_api_key"
        )
        self.assertTrue(blocker["fatal"])
        self.assertEqual(blocker["count"], 1)
        target = self.root / "default-target"
        with self.assertRaises(MigrationError) as caught:
            convert(self.source, self.uploads, self.template, target)
        self.assertEqual(caught.exception.code, "fatal_source_input")
        self.assertFalse(target.exists())

    def test_opt_in_preserves_exact_key_on_matching_legacy_user_only(self):
        add_api_key_source(self.source, data=None)
        source_hash, template_hash = digest(self.source), digest(self.template)
        report = preview(self.source, self.uploads, self.template, include_api_keys=True)
        self.assertTrue(report["api_key_import"]["eligible"])
        self.assertEqual(report["api_key_import"]["selected_key_count"], 1)
        self.assertFalse(report["cutover_eligible"])
        blocker_codes = {item["code"] for item in report["blockers"]}
        self.assertIn("api_key_record_metadata_not_preserved", blocker_codes)
        self.assertIn("unsupported_nonempty_domain_config", blocker_codes)
        self.assertNotIn("unsupported_nonempty_domain_api_key", blocker_codes)

        target = self.root / "key-target"
        converted = convert(
            self.source,
            self.uploads,
            self.template,
            target,
            include_api_keys=True,
        )
        self.assertFalse(converted["cutover_eligible"])
        self.assertEqual(converted["imported_counts"]["user"], 1)
        self.assertEqual(digest(self.source), source_hash)
        self.assertEqual(digest(self.template), template_hash)
        app_db = target / "webui.db"
        with sqlite3.connect(app_db) as db:
            row = db.execute(
                'SELECT id,name,email,role,api_key,settings,info FROM "user" WHERE id=?',
                ("u1",),
            ).fetchone()
            config_count = db.execute('SELECT COUNT(*) FROM "config"').fetchone()[0]
        self.assertEqual(row[0:4], ("u1", "Synthetic User", "u1@example.invalid", "user"))
        self.assertEqual(row[4], SYNTHETIC_KEY)
        self.assertEqual(row[5:], ("{}", "{}"))
        self.assertEqual(config_count, 0)
        self.assertEqual(converted["archive"]["exact_source_copy"], True)
        self.assertEqual(digest(Path(converted["archive"]["path"]) / "source.sqlite"), source_hash)
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(converted, sort_keys=True))

    def test_empty_or_absent_key_domain_needs_no_policy_config(self):
        report = preview(self.source, self.uploads, self.template, include_api_keys=True)
        self.assertTrue(report["api_key_import"]["eligible"])
        self.assertEqual(report["api_key_import"]["selected_key_count"], 0)

        db = sqlite3.connect(self.source)
        db.execute(
            "CREATE TABLE api_key(id TEXT,user_id TEXT,key TEXT,data TEXT,expires_at INTEGER,last_used_at INTEGER,created_at INTEGER,updated_at INTEGER)"
        )
        db.commit()
        db.close()
        empty = preview(self.source, self.uploads, self.template, include_api_keys=True)
        self.assertTrue(empty["api_key_import"]["eligible"])
        self.assertEqual(empty["api_key_import"]["selected_key_count"], 0)

    def test_policy_gates_fail_closed_before_target_creation(self):
        cases = [
            ("disabled_api_key", {**POLICY, "auth.enable_api_keys": False}),
            ("disabled_feature_permission", {**POLICY, "user.permissions": {"features": {"api_keys": False}}}),
            ("restricted_endpoints", {**POLICY, "auth.api_key.endpoint_restrictions": True}),
            ("nonempty_allowed_endpoints", {**POLICY, "auth.api_key.allowed_endpoints": ["/api/v1/chat/completions"]}),
            ("missing_policy", {key: value for key, value in POLICY.items() if key != "auth.enable_api_keys"}),
        ]
        for label, policy in cases:
            with self.subTest(label=label):
                self._new_fixture()
                try:
                    add_api_key_source(self.source, policy=policy)
                    self._assert_refused_before_target()
                finally:
                    self.temp.cleanup()

    def test_duplicate_or_ambiguous_policy_config_refuses(self):
        self._new_fixture()
        add_api_key_source(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                ("auth.enable_api_keys", "false"),
            )
        self._assert_refused_before_target()

        self._new_fixture()
        add_api_key_source(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                ("user.permissions.features.api_keys", "false"),
            )
        self._assert_refused_before_target()

    def test_admin_owner_is_preserved_without_changing_key_value(self):
        add_api_key_source(self.source, owner="admin-user")
        with sqlite3.connect(self.source) as db:
            db.execute(
                'INSERT INTO "user"(id,name,email,role,created_at,updated_at,settings,info) VALUES (?,?,?,?,?,?,?,?)',
                ("admin-user", "Synthetic Admin", "admin@example.invalid", "admin", "1", "2", "{}", "{}"),
            )
            db.execute(
                'INSERT INTO "auth"(id,email,password,active) VALUES (?,?,?,?)',
                ("admin-user", "admin@example.invalid", "synthetic-hash", 1),
            )
        target = self.root / "admin-target"
        convert(self.source, self.uploads, self.template, target, include_api_keys=True)
        with sqlite3.connect(target / "webui.db") as db:
            key = db.execute('SELECT api_key FROM "user" WHERE id=?', ("admin-user",)).fetchone()[0]
        self.assertEqual(key, SYNTHETIC_KEY)

    def test_record_shape_and_owner_failures_refuse_before_target(self):
        cases = [
            ("malformed_key", {"key": "sha256$hash"}),
            ("expired_key", {"expires_at": 1800000000}),
            ("nonempty_key_data", {"data": '{"restriction":"synthetic"}'}),
            ("unknown_column", {"table_columns": "id TEXT,user_id TEXT,key TEXT,data TEXT,expires_at INTEGER,last_used_at INTEGER,created_at INTEGER,updated_at INTEGER,scope TEXT"}),
            ("missing_owner", {"owner": "missing-user"}),
        ]
        for label, options in cases:
            with self.subTest(label=label):
                self._new_fixture()
                try:
                    add_api_key_source(self.source, **options)
                    self._assert_refused_before_target()
                finally:
                    self.temp.cleanup()

    def test_inactive_and_pending_owners_refuse_before_target(self):
        for label, sql in (
            ("inactive", "UPDATE auth SET active=0 WHERE id='u1'"),
            ("pending", "UPDATE user SET role='pending' WHERE id='u1'"),
        ):
            with self.subTest(label=label):
                self._new_fixture()
                try:
                    add_api_key_source(self.source)
                    with sqlite3.connect(self.source) as db:
                        db.execute(sql)
                    self._assert_refused_before_target()
                finally:
                    self.temp.cleanup()

    def test_duplicate_owner_or_key_refuses_before_target(self):
        for label, second_owner, second_key in (
            ("duplicate_owner", "u1", "sk-" + "b" * 32),
            ("duplicate_key", "u2", SYNTHETIC_KEY),
        ):
            with self.subTest(label=label):
                self._new_fixture()
                try:
                    add_api_key_source(self.source)
                    if second_owner == "u2":
                        add_user(self.source, "u2")
                    with sqlite3.connect(self.source) as db:
                        db.execute(
                            "INSERT INTO api_key VALUES (?,?,?,?,?,?,?,?)",
                            ("synthetic-second-row", second_owner, second_key, None, None, None, 1700000002, 1700000003),
                        )
                    self._assert_refused_before_target()
                finally:
                    self.temp.cleanup()

    def test_nonempty_group_permission_override_refuses(self):
        add_api_key_source(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute('DROP TABLE "group"')
            db.execute(
                'CREATE TABLE "group"(id TEXT,user_id TEXT,name TEXT,description TEXT,data TEXT,meta TEXT,permissions TEXT,created_at INTEGER,updated_at INTEGER,parent_group_id TEXT)'
            )
            db.execute(
                'INSERT INTO "group" VALUES (?,?,?,?,?,?,?,?,?,NULL)',
                ("g1", "u1", "Synthetic group", "Synthetic", '{"config":{"share":"members"}}', None, '{"api_keys":false}', 1, 2),
            )
        self._assert_refused_before_target()

    def test_cli_supports_preview_and_convert_flag_without_logging_key(self):
        add_api_key_source(self.source)
        script = Path(__file__).with_name("migrate_sqlite.py")
        common = [
            sys.executable, str(script),
            "--source-db", str(self.source),
            "--uploads-root", str(self.uploads),
            "--empty-old-target-template", str(self.template),
        ]
        preview_result = subprocess.run(
            common[:2] + ["preview"] + common[2:] + ["--include-api-keys"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(preview_result.returncode, 3, preview_result.stderr)
        preview_report = json.loads(preview_result.stdout)
        self.assertEqual(preview_report["api_key_import"]["selected_key_count"], 1)
        self.assertNotIn(SYNTHETIC_KEY, preview_result.stdout)

        target = self.root / "cli-target"
        convert_result = subprocess.run(
            common[:2] + ["convert"] + common[2:]
            + ["--new-target-dir", str(target), "--include-api-keys"],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(convert_result.returncode, 3, convert_result.stderr)
        report = json.loads(convert_result.stdout)
        self.assertFalse(report["cutover_eligible"])
        self.assertNotIn(SYNTHETIC_KEY, convert_result.stdout)
        with sqlite3.connect(target / "webui.db") as db:
            self.assertEqual(
                db.execute('SELECT api_key FROM "user" WHERE id="u1"').fetchone()[0],
                SYNTHETIC_KEY,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
