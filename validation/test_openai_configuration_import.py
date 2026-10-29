#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""SQLite and CLI controls for the narrow OpenAI-compatible config import."""

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
from test_migrate_sqlite import add_named_tool_configuration, build_source, build_template, digest
from test_user_permissions_import import add_permissions


FLAG = "--include-openai-configuration"
SYNTHETIC_API_KEY = "sk-openatelier-synthetic-configuration"
OPENAI_SETTINGS = {
    "openai.enable": True,
    "openai.api_base_urls": ["http://127.0.0.1:18081/v1"],
    "openai.api_keys": [SYNTHETIC_API_KEY],
    "openai.api_configs": {},
}


def add_openai_settings(source: Path, values: dict | None = None, *, duplicate: str | None = None) -> None:
    values = OPENAI_SETTINGS if values is None else values
    with sqlite3.connect(source) as db:
        for key, value in values.items():
            raw = value if isinstance(value, str) and value.startswith("RAW:") else json.dumps(value)
            if isinstance(raw, str) and raw.startswith("RAW:"):
                raw = raw[4:]
            db.execute(
                'INSERT INTO config("key","value",updated_at) VALUES (?,?,NULL)',
                (key, raw),
            )
        if duplicate is not None:
            db.execute(
                'INSERT INTO config("key","value",updated_at) VALUES (?,?,NULL)',
                (duplicate, json.dumps(values[duplicate])),
            )


class OpenAIConfigurationImportTests(unittest.TestCase):
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

    def test_default_off_keeps_selected_provider_rows_archived(self):
        add_openai_settings(self.source)
        before = digest(self.source)
        report = preview(self.source, self.uploads, self.template)
        self.assertNotIn("openai_configuration", report)
        blocker = next(item for item in report["blockers"] if item["code"] == "unsupported_nonempty_domain_config")
        self.assertEqual(blocker["count"], 4)
        self.assertFalse(report["cutover_eligible"])
        self.assertEqual(digest(self.source), before)

    def test_cli_preview_convert_and_secret_redaction(self):
        add_openai_settings(self.source)
        before = digest(self.source)
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
            [common[0], str(script), "preview", *common[2:]],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(preview_result.returncode, 3, preview_result.stderr)
        preview_report = json.loads(preview_result.stdout)
        self.assertEqual(preview_report["openai_configuration"]["selected_key_count"], 4)
        self.assertEqual(preview_report["openai_configuration"]["remaining_config_rows"], 0)
        self.assertIn(
            "openai_configuration_prototype",
            {item["code"] for item in preview_report["blockers"]},
        )
        self.assertFalse(preview_report["cutover_eligible"])
        self.assertNotIn(SYNTHETIC_API_KEY, preview_result.stdout)

        target = self.root / "converted"
        converted = subprocess.run(
            [common[0], str(script), "convert", *common[2:], "--new-target-dir", str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(converted.returncode, 3, converted.stderr)
        self.assertNotIn(SYNTHETIC_API_KEY, converted.stdout + converted.stderr)
        config = self.target_config(target)
        self.assertEqual(
            config["openai"],
            {
                "enable": True,
                "api_base_urls": OPENAI_SETTINGS["openai.api_base_urls"],
                "api_keys": [SYNTHETIC_API_KEY],
                "api_configs": {},
            },
        )
        self.assertEqual(config["openai"]["enable"], True)
        self.assertEqual(config["openai"]["api_base_urls"], OPENAI_SETTINGS["openai.api_base_urls"])
        self.assertEqual(config["openai"]["api_keys"], [SYNTHETIC_API_KEY])
        self.assertEqual(config["openai"]["api_configs"], {})
        self.assertEqual(config["version"], 0)
        self.assertEqual(digest(self.source), before)

    def test_false_enablement_and_selected_mapping_preserve_exact_values(self):
        values = dict(OPENAI_SETTINGS)
        values["openai.enable"] = False
        add_openai_settings(self.source, values)
        report = preview(
            self.source, self.uploads, self.template, include_openai_configuration=True
        )
        self.assertEqual(report["openai_configuration"]["selected_key_count"], 4)
        self.assertEqual(report["openai_configuration"]["remaining_config_rows"], 0)
        target = self.root / "false-enable"
        convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        self.assertFalse(self.target_config(target)["openai"]["enable"])

    def test_static_headers_are_preserved_exactly_in_selected_endpoint_options(self):
        values = dict(OPENAI_SETTINGS)
        values["openai.api_base_urls"] = [
            "http://127.0.0.1:18081/v1",
            "http://127.0.0.1:18082/v1",
        ]
        values["openai.api_keys"] = [SYNTHETIC_API_KEY, "sk-openatelier-second-synthetic"]
        values["openai.api_configs"] = {
            "0": {
                "enable": True,
                "headers": {
                    "Origin": "http://localhost:3000",
                    "Authorization": "Bearer synthetic-operator-value",
                },
            },
            "1": {"enable": True},
        }
        add_openai_settings(self.source, values)
        report = preview(
            self.source, self.uploads, self.template, include_openai_configuration=True
        )
        self.assertEqual(report["openai_configuration"]["selected_key_count"], 4)
        self.assertNotIn("synthetic-operator-value", json.dumps(report))
        self.assertNotIn(SYNTHETIC_API_KEY, json.dumps(report))
        target = self.root / "static-headers"
        convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        self.assertEqual(
            self.target_config(target)["openai"]["api_configs"],
            values["openai.api_configs"],
        )
        self.assertEqual(
            self.target_config(target)["openai"]["api_base_urls"],
            values["openai.api_base_urls"],
        )
        self.assertEqual(
            self.target_config(target)["openai"]["api_keys"], values["openai.api_keys"]
        )

    def test_model_ids_and_empty_prefix_preserve_exact_selected_options(self):
        model_ids = [f"{index:02d}" + "x" * 126 for index in range(64)]
        values = {
            **OPENAI_SETTINGS,
            "openai.api_base_urls": [
                "http://127.0.0.1:18081/v1",
                "http://127.0.0.1:18082/v1",
            ],
            "openai.api_keys": [SYNTHETIC_API_KEY, "sk-second-synthetic-key"],
            "openai.api_configs": {
                "0": {"enable": True, "model_ids": ["community-qwen"], "prefix_id": ""},
                "1": {"enable": False, "model_ids": model_ids, "prefix_id": ""},
            },
        }
        add_openai_settings(self.source, values)
        before = digest(self.source)
        report = preview(
            self.source, self.uploads, self.template, include_openai_configuration=True
        )
        self.assertFalse(report["fatal"])
        self.assertEqual(report["openai_configuration"]["selected_key_count"], 4)
        for private_value in (SYNTHETIC_API_KEY, "community-qwen", model_ids[0], "sk-second-synthetic-key"):
            self.assertNotIn(private_value, json.dumps(report))

        target = self.root / "model-options"
        convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        imported = self.target_config(target)["openai"]
        self.assertEqual(imported["api_configs"], values["openai.api_configs"])
        self.assertEqual(imported["api_base_urls"], values["openai.api_base_urls"])
        self.assertEqual(imported["api_keys"], values["openai.api_keys"])
        self.assertEqual(digest(self.source), before)

    def test_invalid_model_options_refuse_before_target_without_echo(self):
        too_many = [f"id-{index}" for index in range(65)]
        too_many_bytes = [f"{index:02d}" + "x" * 254 for index in range(33)]
        invalid_entries = (
            {"enable": True, "model_ids": []},
            {"enable": True, "model_ids": "one-string"},
            {"enable": True, "model_ids": too_many},
            {"enable": True, "model_ids": ["x" * 257]},
            {"enable": True, "model_ids": too_many_bytes},
            {"enable": True, "model_ids": ["has whitespace"]},
            {"enable": True, "model_ids": ["control\x1f"]},
            {"enable": True, "model_ids": ["control\x85"]},
            {"enable": True, "model_ids": ["control\x7f"]},
            {"enable": True, "model_ids": ["malformed\ud800"]},
            {"enable": True, "model_ids": [True]},
            {"enable": True, "model_ids": [{"id": "nested"}]},
            {"enable": True, "model_ids": ["private-model-marker", "private-model-marker"]},
            {"enable": True, "prefix_id": "nonempty"},
            {"enable": True, "prefix_id": None},
            {"enable": True, "tags": []},
            {"enable": True, "model_ids": ["valid"], "unexpected": False},
        )
        for entry in invalid_entries:
            with self.subTest(keys=sorted(entry)):
                self.tearDown()
                self.setUp()
                values = {**OPENAI_SETTINGS, "openai.api_configs": {"0": entry}}
                add_openai_settings(self.source, values)
                before = digest(self.source)
                target = self.root / "refused-model-options"
                with self.assertRaises(MigrationError) as caught:
                    convert(
                        self.source, self.uploads, self.template, target,
                        include_openai_configuration=True,
                    )
                self.assertEqual(caught.exception.code, "fatal_source_input")
                report = json.dumps(caught.exception.report)
                self.assertIn("openai_configuration_value_unsupported", report)
                self.assertNotIn(SYNTHETIC_API_KEY, report)
                self.assertNotIn("private-model-marker", report)
                self.assertFalse(target.exists())
                self.assertEqual(digest(self.source), before)

    def test_duplicate_model_ids_across_endpoints_refuse(self):
        values = {
            **OPENAI_SETTINGS,
            "openai.api_base_urls": ["http://127.0.0.1:18081/v1", "http://127.0.0.1:18082/v1"],
            "openai.api_keys": [SYNTHETIC_API_KEY, "sk-second-synthetic-key"],
            "openai.api_configs": {
                "0": {"enable": True, "model_ids": ["duplicate-model"]},
                "1": {"enable": True, "model_ids": ["duplicate-model"]},
            },
        }
        add_openai_settings(self.source, values)
        self.assert_refused_before_target("openai_configuration_value_unsupported")

    def test_composes_with_existing_named_api_key_and_permission_selectors(self):
        add_named_tool_configuration(self.source)
        add_api_key_settings(self.source)
        add_permissions(self.source)
        add_openai_settings(self.source)
        report = preview(
            self.source, self.uploads, self.template,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
            include_user_permissions=True,
            include_openai_configuration=True,
        )
        self.assertEqual(report["table_counts"]["config"], 9)
        self.assertEqual(report["openai_configuration"]["remaining_config_rows"], 0)
        self.assertEqual(report["api_key_configuration"]["remaining_config_rows"], 0)
        self.assertEqual(report["named_tool_configuration"]["remaining_config_rows"], 0)
        self.assertEqual(report["user_permissions"]["remaining_config_rows"], 0)
        target = self.root / "composed"
        convert(
            self.source, self.uploads, self.template, target,
            include_named_tool_configuration=True,
            include_api_key_configuration=True,
            include_user_permissions=True,
            include_openai_configuration=True,
        )
        config = self.target_config(target)
        self.assertEqual(config["openai"]["api_keys"], [SYNTHETIC_API_KEY])
        self.assertTrue(config["auth"]["api_key"]["enable"])
        self.assertEqual(len(config["tool_server"]["connections"]), 1)
        self.assertIs(config["user"]["permissions"]["features"]["api_keys"], False)

    def assert_refused_before_target(self, expected_code: str):
        before = digest(self.source)
        target = self.root / "refused"
        with self.assertRaises(MigrationError) as caught:
            convert(
                self.source, self.uploads, self.template, target,
                include_openai_configuration=True,
            )
        self.assertEqual(caught.exception.code, "fatal_source_input")
        self.assertIn(expected_code, {item["code"] for item in caught.exception.report["blockers"]})
        self.assertFalse(target.exists())
        self.assertNotIn(SYNTHETIC_API_KEY, json.dumps(caught.exception.report))
        self.assertNotIn("synthetic-static-secret", json.dumps(caught.exception.report))
        self.assertEqual(digest(self.source), before)

    def test_alias_parent_descendant_and_duplicate_rows_refuse_before_target(self):
        cases = (
            ("old_enable_alias", {**OPENAI_SETTINGS, "ENABLE_OPENAI_API": True}, "openai_configuration_ambiguous_override"),
            ("old_key_alias", {**OPENAI_SETTINGS, "OPENAI_API_KEY": "RAW:\"redacted\""}, "openai_configuration_ambiguous_override"),
            ("singular_path_alias", {**OPENAI_SETTINGS, "openai.api_key": ["RAW:synthetic"]}, "openai_configuration_ambiguous_override"),
            ("parent", {**OPENAI_SETTINGS, "openai": {}}, "openai_configuration_ambiguous_override"),
            ("descendant", {**OPENAI_SETTINGS, "openai.api_configs.extra": {}}, "openai_configuration_ambiguous_override"),
            ("selected_child", {**OPENAI_SETTINGS, "openai.api_keys.primary": []}, "openai_configuration_ambiguous_override"),
            ("missing", {key: value for key, value in OPENAI_SETTINGS.items() if key != "openai.enable"}, "openai_configuration_missing_key"),
        )
        for name, values, code in cases:
            with self.subTest(name=name):
                self.tearDown()
                self.setUp()
                add_openai_settings(self.source, values)
                self.assert_refused_before_target(code)

        self.tearDown()
        self.setUp()
        add_openai_settings(self.source, duplicate="openai.enable")
        self.assert_refused_before_target("openai_configuration_duplicate_key")

    def test_wrong_types_unsupported_shapes_and_unrelated_siblings(self):
        invalid_cases = (
            ({**OPENAI_SETTINGS, "openai.enable": "RAW:\"true\""}, "openai_configuration_value_invalid"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": "RAW:\"http://127.0.0.1/v1\""}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": ["https://u:p@example.invalid/v1"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": ["https://example.invalid/v1?query=x"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": ["https://example.invalid/v1#frag"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": ["http://example.invalid/ bad"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_base_urls": ["ftp://example.invalid/v1"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_keys": []}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_keys": ["bad key"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_keys": ["clé"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_keys": [SYNTHETIC_API_KEY, "second"]}, "openai_configuration_value_unsupported"),
            ({**OPENAI_SETTINGS, "openai.api_configs": {"timeout": 1}}, "openai_configuration_value_unsupported"),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"bad name": "x"}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"Origin": "http://ok\r\nX-Injected: synthetic-static-secret"}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"Origin": "{{ user }}"}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"X-Test": "control\x85"}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"Origin": "a", "origin": "b"}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {"Origin": "x" * 4097}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {f"X-{index}": "v" for index in range(33)}}}},
                "openai_configuration_value_unsupported",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "headers": {f"X-{index}": "v" * 4096 for index in range(5)}}}},
                "openai_configuration_value_invalid",
            ),
            (
                {**OPENAI_SETTINGS, "openai.api_configs": {"0": {"enable": True, "unexpected": True}}},
                "openai_configuration_value_unsupported",
            ),
            ({**OPENAI_SETTINGS, "openai.api_configs": "RAW:{\"x\":1,\"x\":2}"}, "openai_configuration_value_invalid"),
            ({**OPENAI_SETTINGS, "openai.enable": "RAW:True"}, "openai_configuration_value_invalid"),
        )
        for values, code in invalid_cases:
            with self.subTest(code=code, setting=list(set(values) - set(OPENAI_SETTINGS)) or "selected"):
                self.tearDown()
                self.setUp()
                add_openai_settings(self.source, values)
                self.assert_refused_before_target(code)

        self.tearDown()
        self.setUp()
        add_openai_settings(self.source)
        with sqlite3.connect(self.source) as db:
            db.execute(
                'INSERT INTO config("key","value",updated_at) VALUES (?,?,NULL)',
                ("openai.other_feature", json.dumps(True)),
            )
        report = preview(
            self.source, self.uploads, self.template, include_openai_configuration=True
        )
        self.assertFalse(report["fatal"])
        self.assertEqual(report["openai_configuration"]["remaining_config_rows"], 1)
        self.assertNotIn(SYNTHETIC_API_KEY, json.dumps(report))

    def test_actual_cli_refuses_unsupported_key_without_echoing_it(self):
        values = dict(OPENAI_SETTINGS)
        values["openai.api_keys"] = ["secret must not appear"]
        add_openai_settings(self.source, values)
        script = Path(__file__).with_name("migrate_sqlite.py")
        result = subprocess.run(
            [
                sys.executable, str(script), "preview",
                "--source-db", str(self.source),
                "--uploads-root", str(self.uploads),
                "--empty-old-target-template", str(self.template),
                FLAG,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertNotIn("secret must not appear", result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn("openai_configuration_value_unsupported", {item["code"] for item in payload["blockers"]})

    def test_multiple_endpoints_preserve_index_order_and_allow_key_reuse(self):
        urls = ["http://127.0.0.1:18081/v1", "http://127.0.0.1:18082/service/v1"]
        keys = ["sk-first-synthetic-key", "sk-second-synthetic-key"]
        values = {
            **OPENAI_SETTINGS,
            "openai.api_base_urls": urls,
            "openai.api_keys": keys,
        }
        add_openai_settings(self.source, values)
        target = self.root / "two-endpoints"
        report = convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        self.assertEqual(report["openai_configuration"]["selected_key_count"], 4)
        config = self.target_config(target)["openai"]
        self.assertEqual(config["api_base_urls"], urls)
        self.assertEqual(config["api_keys"], keys)
        self.assertEqual(config["api_configs"], {})

        self.tearDown()
        self.setUp()
        values["openai.api_keys"] = [keys[0], keys[0]]
        add_openai_settings(self.source, values)
        report = preview(
            self.source, self.uploads, self.template,
            include_openai_configuration=True,
        )
        self.assertFalse(report["fatal"])

    def test_endpoint_list_bounds_and_pairing_refuse_before_target(self):
        base = "http://127.0.0.1:18081/v1"
        cases = (
            ({"openai.api_base_urls": [], "openai.api_keys": []}, "openai_configuration_value_unsupported"),
            ({"openai.api_base_urls": [base, "http://127.0.0.1:18082/v1"], "openai.api_keys": ["sk-one"]}, "openai_configuration_value_unsupported"),
            ({"openai.api_base_urls": [base, base], "openai.api_keys": ["sk-one", "sk-two"]}, "openai_configuration_value_unsupported"),
            ({"openai.api_base_urls": [base, "ftp://example.invalid/v1"], "openai.api_keys": ["sk-one", "sk-two"]}, "openai_configuration_value_unsupported"),
            ({"openai.api_base_urls": [base], "openai.api_keys": ["sk-one", "sk-two"]}, "openai_configuration_value_unsupported"),
        )
        for replacement, expected in cases:
            with self.subTest(replacement=replacement):
                self.tearDown()
                self.setUp()
                values = {**OPENAI_SETTINGS, **replacement}
                add_openai_settings(self.source, values)
                self.assert_refused_before_target(expected)

        self.tearDown()
        self.setUp()
        urls = [f"http://127.0.0.1:{18081 + index}/v1" for index in range(9)]
        add_openai_settings(
            self.source,
            {**OPENAI_SETTINGS, "openai.api_base_urls": urls, "openai.api_keys": ["sk-key"] * len(urls)},
        )
        self.assert_refused_before_target("openai_configuration_value_unsupported")

    def test_eight_endpoints_are_bounded_and_preserved(self):
        urls = [f"http://127.0.0.1:{18081 + index}/v1" for index in range(8)]
        keys = [f"sk-synthetic-key-{index}" for index in range(8)]
        add_openai_settings(
            self.source,
            {**OPENAI_SETTINGS, "openai.api_base_urls": urls, "openai.api_keys": keys},
        )
        target = self.root / "eight-endpoints"
        convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        config = self.target_config(target)["openai"]
        self.assertEqual(config["api_base_urls"], urls)
        self.assertEqual(config["api_keys"], keys)

    def test_complete_indexed_enable_map_preserves_false_and_true(self):
        urls = ["http://127.0.0.1:18081/v1", "http://127.0.0.1:18082/v1"]
        keys = ["sk-index-zero", "sk-index-one"]
        states = {"0": {"enable": False}, "1": {"enable": True}}
        add_openai_settings(
            self.source,
            {
                **OPENAI_SETTINGS,
                "openai.api_base_urls": urls,
                "openai.api_keys": keys,
                "openai.api_configs": states,
            },
        )
        target = self.root / "indexed-enable-map"
        report = convert(
            self.source, self.uploads, self.template, target,
            include_openai_configuration=True,
        )
        self.assertEqual(report["openai_configuration"]["selected_key_count"], 4)
        config = self.target_config(target)["openai"]
        self.assertEqual(config["api_base_urls"], urls)
        self.assertEqual(config["api_keys"], keys)
        self.assertEqual(config["api_configs"], states)
        self.assertIs(config["api_configs"]["0"]["enable"], False)
        self.assertIs(config["api_configs"]["1"]["enable"], True)

    def test_sparse_or_unmeasured_endpoint_enable_maps_refuse_before_target(self):
        urls = ["http://127.0.0.1:18081/v1", "http://127.0.0.1:18082/v1"]
        keys = ["sk-index-zero", "sk-index-one"]
        invalid_maps = (
            {"0": {"enable": False}},
            {"0": {"enable": False}, "2": {"enable": True}},
            {"00": {"enable": False}, "1": {"enable": True}},
            {"0": {"enable": False}, "1": {"enabled": True}},
            {"0": {"enable": False}, "1": {"enable": True, "timeout": 1}},
            {"0": {"enable": "false"}, "1": {"enable": True}},
            {"0": [False], "1": {"enable": True}},
            [False, True],
        )
        for config_map in invalid_maps:
            with self.subTest(config_map=config_map):
                self.tearDown()
                self.setUp()
                add_openai_settings(
                    self.source,
                    {
                        **OPENAI_SETTINGS,
                        "openai.api_base_urls": urls,
                        "openai.api_keys": keys,
                        "openai.api_configs": config_map,
                    },
                )
                self.assert_refused_before_target("openai_configuration_value_unsupported")


if __name__ == "__main__":
    unittest.main(verbosity=2)
