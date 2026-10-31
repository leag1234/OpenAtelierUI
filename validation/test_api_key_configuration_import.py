#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""SQLite and CLI controls for the opt-in API-key configuration import."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from migrate_sqlite import MigrationError, convert, preview
from test_migrate_sqlite import add_named_tool_configuration, build_source, build_template, digest


SETTINGS = {
    "auth.enable_api_keys": True,
    "auth.api_key.endpoint_restrictions": False,
    "auth.api_key.allowed_endpoints": "",
}
FLAG = "--include-api-key-configuration"


def add_api_key_settings(source: Path, values: dict | None = None) -> None:
    values = SETTINGS if values is None else values
    with sqlite3.connect(source) as db:
        for key, value in values.items():
            raw = value if isinstance(value, str) and value.startswith("RAW:") else json.dumps(value)
            if isinstance(raw, str) and raw.startswith("RAW:"):
                raw = raw[4:]
            db.execute(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                (key, raw),
            )


def add_unrelated_auth_settings(source: Path) -> None:
    """Add auth settings outside the selected API-key policy paths."""
    with sqlite3.connect(source) as db:
        db.executemany(
            "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
            (
                ("auth.jwt_expiry", json.dumps("4w")),
                ("auth.oauth.synthetic_enabled", json.dumps(False)),
            ),
        )


class ApiKeyConfigurationImportTests(unittest.TestCase):
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

    def test_default_off_retains_three_rows_as_unmapped_configuration(self):
        add_api_key_settings(self.source)
        report = preview(self.source, self.uploads, self.template)
        self.assertNotIn("api_key_configuration", report)
        blocker = next(
            item for item in report["blockers"]
            if item["code"] == "unsupported_nonempty_domain_config"
        )
        self.assertEqual(blocker["count"], 3)
        self.assertFalse(report["cutover_eligible"])

    def test_enable_true_and_false_map_exactly_into_old_nested_config(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                if enabled is False:
                    self.tearDown()
                    self.setUp()
                values = dict(SETTINGS)
                values["auth.enable_api_keys"] = enabled
                add_api_key_settings(self.source, values)
                before_source = digest(self.source)
                report = preview(
                    self.source,
                    self.uploads,
                    self.template,
                    include_api_key_configuration=True,
                )
                self.assertEqual(report["api_key_configuration"]["selected_key_count"], 3)
                self.assertEqual(report["api_key_configuration"]["remaining_config_rows"], 0)
                self.assertFalse(report["cutover_eligible"])
                self.assertIn(
                    "api_key_configuration_prototype",
                    {item["code"] for item in report["blockers"]},
                )
                target = self.root / "converted"
                converted = convert(
                    self.source,
                    self.uploads,
                    self.template,
                    target,
                    include_api_key_configuration=True,
                )
                data = self.target_config(target)
                self.assertEqual(
                    data["auth"]["api_key"],
                    {
                        "enable": enabled,
                        "endpoint_restrictions": False,
                        "allowed_endpoints": "",
                    },
                )
                self.assertEqual(data["version"], 0)
                self.assertEqual(converted["imported_counts"]["config"], 1)
                self.assertFalse(converted["cutover_eligible"])
                self.assertEqual(digest(self.source), before_source)

    def test_named_connection_and_api_key_settings_share_one_old_config_record(self):
        add_named_tool_configuration(self.source, other_rows=2)
        add_api_key_settings(self.source)
        report = preview(
            self.source,
            self.uploads,
            self.template,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
        )
        self.assertEqual(report["table_counts"]["config"], 6)
        self.assertEqual(report["named_tool_configuration"]["remaining_config_rows"], 2)
        self.assertEqual(report["api_key_configuration"]["remaining_config_rows"], 2)
        self.assertIn(
            ("unsupported_nonempty_domain_config", 2),
            {(item["code"], item["count"]) for item in report["blockers"]},
        )
        target = self.root / "combined"
        convert(
            self.source,
            self.uploads,
            self.template,
            target,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
        )
        data = self.target_config(target)
        self.assertEqual(data["auth"]["api_key"]["enable"], True)
        self.assertEqual(len(data["tool_server"]["connections"]), 1)
        self.assertEqual(data["tool_server"]["connections"][0]["info"]["id"], "synthetic_openapi")

    def assert_refused_before_target(self, expected_code: str):
        source_digest = digest(self.source)
        target = self.root / "refused"
        with self.assertRaises(MigrationError) as caught:
            convert(
                self.source,
                self.uploads,
                self.template,
                target,
                include_api_key_configuration=True,
            )
        self.assertEqual(caught.exception.code, "fatal_source_input")
        self.assertIn(expected_code, {item["code"] for item in caught.exception.report["blockers"]})
        self.assertFalse(target.exists())
        self.assertEqual(digest(self.source), source_digest)

    def test_missing_duplicate_and_overlapping_rows_refuse_before_target(self):
        cases = (
            ("missing", None, "api_key_configuration_missing_key"),
            ("old_alias", {**SETTINGS, "ENABLE_API_KEY": True}, "api_key_configuration_ambiguous_override"),
            ("parent", {**SETTINGS, "auth.api_key": {}}, "api_key_configuration_ambiguous_override"),
            ("old_nested_alias", {**SETTINGS, "auth.api_key.enable": True}, "api_key_configuration_ambiguous_override"),
            ("dotted_child", {**SETTINGS, "auth.enable_api_keys.extra": True}, "api_key_configuration_ambiguous_override"),
            ("selected_dotted_child", {**SETTINGS, "auth.api_key.endpoint_restrictions.extra": False}, "api_key_configuration_ambiguous_override"),
        )
        for name, values, code in cases:
            with self.subTest(name=name):
                if name != "missing":
                    self.tearDown()
                    self.setUp()
                    add_api_key_settings(self.source, values)
                self.assert_refused_before_target(code)

        self.tearDown()
        self.setUp()
        add_api_key_settings(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute(
                "INSERT INTO config(key,value,updated_at) VALUES (?,?,NULL)",
                ("auth.enable_api_keys", "false"),
            )
        self.assert_refused_before_target("api_key_configuration_duplicate_key")

    def test_strict_value_shapes_reject_before_target(self):
        cases = (
            ("string_boolean", {**SETTINGS, "auth.enable_api_keys": "RAW:\"true\""}, "api_key_configuration_value_invalid"),
            ("string_restriction", {**SETTINGS, "auth.api_key.endpoint_restrictions": "RAW:\"false\""}, "api_key_configuration_value_unsupported"),
            ("restricted_true", {**SETTINGS, "auth.api_key.endpoint_restrictions": True}, "api_key_configuration_value_unsupported"),
            ("nonempty_allowlist", {**SETTINGS, "auth.api_key.allowed_endpoints": ["/synthetic"]}, "api_key_configuration_value_unsupported"),
            ("nonempty_allowlist_string", {**SETTINGS, "auth.api_key.allowed_endpoints": "/synthetic"}, "api_key_configuration_value_unsupported"),
            ("duplicate_json_key", {**SETTINGS, "auth.api_key.allowed_endpoints": 'RAW:{"a":"x","a":"y"}'}, "api_key_configuration_value_invalid"),
            ("malformed_json", {**SETTINGS, "auth.enable_api_keys": "RAW:tru"}, "api_key_configuration_value_invalid"),
            ("oversized_value", {**SETTINGS, "auth.enable_api_keys": "RAW:" + " " * (16 * 1024 + 1)}, "api_key_configuration_value_invalid"),
        )
        for name, values, code in cases:
            with self.subTest(name=name):
                if name != "string_boolean":
                    self.tearDown()
                    self.setUp()
                add_api_key_settings(self.source, values)
                self.assert_refused_before_target(code)

    def test_unsupported_config_table_schema_refuses_before_target(self):
        add_api_key_settings(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute("ALTER TABLE config ADD COLUMN synthetic_extra TEXT")
        self.assert_refused_before_target("api_key_configuration_source_schema_mismatch")

    def test_cli_preview_and_convert_accept_the_opt_in_without_values_in_report(self):
        add_api_key_settings(self.source)
        add_unrelated_auth_settings(self.source)
        script = Path(__file__).with_name("migrate_sqlite.py")
        common = [
            sys.executable,
            str(script),
            "--source-db", str(self.source),
            "--uploads-root", str(self.uploads),
            "--empty-old-target-template", str(self.template),
            FLAG,
        ]
        preview_result = subprocess.run(
            [*common[:1], str(script), "preview", *common[2:]],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(preview_result.returncode, 3, preview_result.stderr)
        preview_report = json.loads(preview_result.stdout)
        self.assertEqual(preview_report["api_key_configuration"]["selected_key_count"], 3)
        self.assertEqual(preview_report["api_key_configuration"]["remaining_config_rows"], 2)
        self.assertFalse(preview_report["fatal"])
        self.assertNotIn("auth.enable_api_keys", preview_result.stdout)

        target = self.root / "cli-target"
        convert_result = subprocess.run(
            [*common[:1], str(script), "convert", *common[2:], "--new-target-dir", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(convert_result.returncode, 3, convert_result.stderr)
        self.assertTrue(target.is_dir())
        self.assertEqual(self.target_config(target)["auth"]["api_key"]["enable"], True)

        # Repeat through the actual CLI with both opt-ins enabled. The
        # unrelated auth siblings remain archive-only while named tools and
        # selected API-key settings share the single old config record.
        self.tearDown()
        self.setUp()
        add_named_tool_configuration(self.source, other_rows=2)
        add_api_key_settings(self.source)
        add_unrelated_auth_settings(self.source)
        combined_common = [
            *common[:3],
            "--source-db", str(self.source),
            "--uploads-root", str(self.uploads),
            "--empty-old-target-template", str(self.template),
            "--include-named-tool-configuration",
            FLAG,
        ]
        combined_preview = subprocess.run(
            [sys.executable, str(script), "preview", *combined_common[3:]],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(combined_preview.returncode, 3, combined_preview.stderr)
        combined_report = json.loads(combined_preview.stdout)
        self.assertFalse(combined_report["fatal"])
        self.assertEqual(combined_report["table_counts"]["config"], 8)
        self.assertEqual(combined_report["api_key_configuration"]["remaining_config_rows"], 4)
        self.assertEqual(combined_report["named_tool_configuration"]["remaining_config_rows"], 4)

        combined_target = self.root / "cli-combined-target"
        combined_convert = subprocess.run(
            [
                sys.executable,
                str(script),
                "convert",
                *combined_common[3:],
                "--new-target-dir",
                str(combined_target),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(combined_convert.returncode, 3, combined_convert.stderr)
        combined_data = self.target_config(combined_target)
        self.assertEqual(combined_data["auth"]["api_key"]["enable"], True)
        self.assertEqual(len(combined_data["tool_server"]["connections"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
