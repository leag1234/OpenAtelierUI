#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""SQLite and CLI controls for the bounded user-permission migration."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from migrate_sqlite import MigrationError, convert, preview
from test_api_key_configuration_import import SETTINGS as API_KEY_SETTINGS
from test_api_key_configuration_import import add_api_key_settings
from test_migrate_sqlite import (
    add_group_import_schema,
    add_named_tool_configuration,
    build_source,
    build_template,
    insert_flat_group,
)


FLAG = "--include-user-permissions"
PERMISSIONS = {
    "workspace": {"models": False, "tools": False},
    "features": {"api_keys": False, "calendar": True},
    "sharing": {"tools": False, "public_tools": False},
}


def add_permissions(source: Path, raw: str | None = None, *, duplicate: bool = False) -> None:
    value = json.dumps(PERMISSIONS, sort_keys=True, separators=(",", ":")) if raw is None else raw
    with sqlite3.connect(source) as db:
        if duplicate:
            db.executemany(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                (("user.permissions", value), ("user.permissions", value)),
            )
        else:
            db.execute(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                ("user.permissions", value),
            )


class UserPermissionsImportTests(unittest.TestCase):
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

    def target_config(self, target: Path) -> dict:
        with sqlite3.connect(target / "webui.db") as db:
            rows = db.execute('SELECT data FROM "config"').fetchall()
        self.assertEqual(len(rows), 1)
        return json.loads(rows[0][0])

    def test_default_off_keeps_permission_row_unmapped(self):
        add_permissions(self.source)
        report = preview(self.source, self.uploads, self.template)
        self.assertNotIn("user_permissions", report)
        self.assertFalse(report["cutover_eligible"])
        blocker = next(
            item for item in report["blockers"]
            if item["code"] == "unsupported_nonempty_domain_config"
        )
        self.assertEqual(blocker["count"], 1)

    def test_opt_in_copies_exact_boolean_map_and_retains_prototype_blocker(self):
        add_permissions(self.source)
        report = preview(
            self.source, self.uploads, self.template, include_user_permissions=True
        )
        self.assertEqual(report["user_permissions"]["selected_key_count"], 1)
        self.assertTrue(report["user_permissions"]["api_keys_permission_present"])
        self.assertFalse(report["cutover_eligible"])
        self.assertIn(
            "user_permissions_prototype",
            {item["code"] for item in report["blockers"]},
        )
        target = self.root / "converted"
        converted = convert(
            self.source,
            self.uploads,
            self.template,
            target,
            include_user_permissions=True,
        )
        self.assertEqual(self.target_config(target)["user"]["permissions"], PERMISSIONS)
        self.assertEqual(converted["imported_counts"]["config"], 1)
        self.assertFalse(converted["cutover_eligible"])

    def test_composes_with_existing_named_and_api_key_config_options(self):
        add_named_tool_configuration(self.source)
        add_api_key_settings(self.source, API_KEY_SETTINGS)
        add_permissions(self.source)
        report = preview(
            self.source,
            self.uploads,
            self.template,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
            include_user_permissions=True,
        )
        self.assertEqual(report["named_tool_configuration"]["selected_connection_count"], 1)
        self.assertEqual(report["api_key_configuration"]["selected_key_count"], 3)
        self.assertEqual(report["user_permissions"]["selected_key_count"], 1)
        target = self.root / "combined"
        convert(
            self.source,
            self.uploads,
            self.template,
            target,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
            include_user_permissions=True,
        )
        data = self.target_config(target)
        self.assertEqual(data["user"]["permissions"], PERMISSIONS)
        self.assertEqual(data["tool_server"]["connections"][0]["info"]["id"], "synthetic_openapi")
        self.assertEqual(data["auth"]["api_key"]["enable"], True)

    def _assert_refused_without_target(self, raw: str | None = None, *, duplicate: bool = False, rows=()):
        add_permissions(self.source, raw, duplicate=duplicate)
        with sqlite3.connect(self.source) as db:
            db.executemany(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                [(key, json.dumps(value)) for key, value in rows],
            )
        target = self.root / "refused"
        with self.assertRaises(MigrationError):
            convert(
                self.source,
                self.uploads,
                self.template,
                target,
                include_user_permissions=True,
            )
        self.assertFalse(target.exists())

    def test_missing_permission_bit_duplicate_json_and_wrong_leaf_types_refuse_pre_target(self):
        cases = (
            '{"features":{"calendar":true}}',
            '{"features":{"api_keys":true,"api_keys":false}}',
            '{"features":{"api_keys":1}}',
            '{"features":{"api_keys":true},"extra":{"enabled":null}}',
        )
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                self.tearDown()
                self.setUp()
                self._assert_refused_without_target(raw)

    def test_duplicate_selected_rows_and_ambiguous_paths_refuse_pre_target(self):
        self._assert_refused_without_target(duplicate=True)
        for key in ("user", "user.permissions.features.api_keys", "USER_PERMISSIONS"):
            with self.subTest(key=key):
                self.tearDown()
                self.setUp()
                self._assert_refused_without_target(rows=((key, {"features": {"api_keys": True}}),))

    def test_oversized_value_refuses_pre_target(self):
        self._assert_refused_without_target(json.dumps({"features": {"api_keys": True}, "padding": "x" * 17000}))

    def test_opt_in_does_not_enable_nonempty_group_permission_overrides(self):
        add_permissions(self.source)
        add_group_import_schema(self.source, self.template)
        insert_flat_group(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute('UPDATE "group" SET permissions=?', ('{"features":{"api_keys":true}}',))
        target = self.root / "refused-group-policy"
        with self.assertRaises(MigrationError):
            convert(
                self.source,
                self.uploads,
                self.template,
                target,
                include_user_permissions=True,
            )
        self.assertFalse(target.exists())

    def test_cli_preview_accepts_the_named_opt_in_and_reports_only_safe_metadata(self):
        add_permissions(self.source)
        script = Path(__file__).with_name("migrate_sqlite.py")
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "preview",
                "--source-db",
                str(self.source),
                "--uploads-root",
                str(self.uploads),
                "--empty-old-target-template",
                str(self.template),
                FLAG,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3)
        output = json.loads(result.stdout)
        self.assertEqual(output["user_permissions"]["selected_key_count"], 1)
        self.assertNotIn('"features":', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
