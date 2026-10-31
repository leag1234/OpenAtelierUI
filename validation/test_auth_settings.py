# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Named tests for the stateless auth-settings manifest and admin DTO field."""

from __future__ import annotations

import ast
import asyncio
import io
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from auth_settings import (
    MAX_DURATION_SECONDS,
    MAX_MANIFEST_BYTES,
    AuthSettingsError,
    load_manifest,
    main,
    validate_manifest_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("auth_settings.py")
ROUTE_SOURCE = ROOT / "backend" / "open_webui" / "routers" / "auths.py"
VALID_MANIFEST = {
    "format": "openatelier-auth-settings/v1",
    "settings": {
        "JWT_EXPIRES_IN": "4w",
        "ENABLE_SIGNUP": False,
        "ENABLE_LOGIN_FORM": True,
    },
}


def _write_manifest(directory: Path, document=VALID_MANIFEST) -> Path:
    path = directory / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class AuthSettingsManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_manifest_projects_only_fixed_environment_and_bounded_receipt(self):
        path = _write_manifest(self.root)
        original = path.read_bytes()
        output = load_manifest(path)
        self.assertEqual(
            output,
            {
                "environment": {
                    "JWT_EXPIRES_IN": "4w",
                    "ENABLE_SIGNUP": "false",
                    "ENABLE_LOGIN_FORM": "true",
                },
                "ttl_seconds": 2_419_200,
                "manifest_sha256": hashlib.sha256(original).hexdigest(),
                "partial": True,
                "cutover": False,
            },
        )
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(output["ttl_seconds"], 4 * 7 * 24 * 60 * 60)
        self.assertLessEqual(output["ttl_seconds"], MAX_DURATION_SECONDS)

    def test_duplicate_json_keys_are_rejected_at_both_levels(self):
        cases = (
            b'{"format":"openatelier-auth-settings/v1","format":"other","settings":{}}',
            b'{"format":"openatelier-auth-settings/v1","settings":{"JWT_EXPIRES_IN":"4w","JWT_EXPIRES_IN":"5w"}}',
        )
        for raw in cases:
            with self.subTest(raw_length=len(raw)):
                with self.assertRaises(AuthSettingsError) as caught:
                    validate_manifest_bytes(raw)
                self.assertEqual(caught.exception.code, "duplicate_json_key")

    def test_wrong_format_missing_or_extra_setting_and_wrong_boolean_types_refuse(self):
        cases = []
        wrong_format = dict(VALID_MANIFEST, format="other")
        cases.append((wrong_format, "unsupported_manifest_format"))
        missing = json.loads(json.dumps(VALID_MANIFEST))
        del missing["settings"]["ENABLE_LOGIN_FORM"]
        cases.append((missing, "manifest_settings_mismatch"))
        extra = json.loads(json.dumps(VALID_MANIFEST))
        extra["settings"]["UNRELATED"] = "value"
        cases.append((extra, "manifest_settings_mismatch"))
        wrong_bool = json.loads(json.dumps(VALID_MANIFEST))
        wrong_bool["settings"]["ENABLE_SIGNUP"] = 0
        cases.append((wrong_bool, "invalid_boolean_setting"))
        for document, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(AuthSettingsError) as caught:
                    validate_manifest_bytes(json.dumps(document).encode())
                self.assertEqual(caught.exception.code, code)

    def test_expiry_requires_strict_positive_bounded_supported_duration(self):
        invalid = (
            ("0", "invalid_expiry_duration"),
            ("-1", "invalid_expiry_duration"),
            ("+4w", "invalid_expiry_duration"),
            ("4w trailing", "invalid_expiry_duration"),
            ("4w1d", "invalid_expiry_duration"),
            ("1ms", "invalid_expiry_duration"),
            ("367d", "expiry_duration_out_of_range"),
            ("999999999999999999w", "expiry_duration_out_of_range"),
            ("1" * 33 + "d", "invalid_expiry_duration"),
        )
        for duration, code in invalid:
            document = json.loads(json.dumps(VALID_MANIFEST))
            document["settings"]["JWT_EXPIRES_IN"] = duration
            with self.subTest(duration=duration[:12]):
                with self.assertRaises(AuthSettingsError) as caught:
                    validate_manifest_bytes(json.dumps(document).encode())
                self.assertEqual(caught.exception.code, code)

    def test_json_invalid_utf8_constants_and_byte_limit_refuse(self):
        for raw, code in (
            (b"{", "invalid_manifest_json"),
            (b"\xff", "invalid_manifest_json"),
            (b'{"x":NaN}', "invalid_json_constant"),
            (b"[" * 2000 + b"]" * 2000, "invalid_manifest_json"),
            (b" " * (MAX_MANIFEST_BYTES + 1), "manifest_too_large"),
        ):
            with self.subTest(code=code):
                with self.assertRaises(AuthSettingsError) as caught:
                    validate_manifest_bytes(raw)
                self.assertEqual(caught.exception.code, code)

    def test_file_loader_refuses_missing_directory_symlink_and_oversize_inputs(self):
        missing = self.root / "missing.json"
        with self.assertRaises(AuthSettingsError) as caught:
            load_manifest(missing)
        self.assertEqual(caught.exception.code, "manifest_unavailable")

        with self.assertRaises(AuthSettingsError) as caught:
            load_manifest(str(self.root / "embedded\0nul.json"))
        self.assertEqual(caught.exception.code, "manifest_unavailable")

        directory = self.root / "directory"
        directory.mkdir()
        with self.assertRaises(AuthSettingsError) as caught:
            load_manifest(directory)
        self.assertEqual(caught.exception.code, "manifest_not_regular_file")

        oversized = self.root / "oversized.json"
        oversized.write_bytes(b" " * (MAX_MANIFEST_BYTES + 1))
        with self.assertRaises(AuthSettingsError) as caught:
            load_manifest(oversized)
        self.assertEqual(caught.exception.code, "manifest_too_large")

        target = _write_manifest(self.root)
        link = self.root / "linked.json"
        try:
            link.symlink_to(target)
        except (NotImplementedError, OSError):
            self.skipTest("symlink creation is unavailable on this test host")
        with self.assertRaises(AuthSettingsError) as caught:
            load_manifest(link)
        self.assertEqual(caught.exception.code, "manifest_symlink_refused")

    def test_cli_is_json_only_named_and_stateless_for_success_and_refusal(self):
        path = _write_manifest(self.root)
        environment_before = dict(os.environ)
        success = subprocess.run(
            [sys.executable, str(SCRIPT), "--manifest", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(success.returncode, 0)
        self.assertEqual(success.stderr, "")
        self.assertEqual(json.loads(success.stdout)["ttl_seconds"], 2_419_200)

        with patch.dict(os.environ, {"JWT_EXPIRES_IN": "unchanged", "ENABLE_SIGNUP": "sentinel"}):
            patched_environment = dict(os.environ)
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["--manifest", str(path)]), 0)
            self.assertEqual(dict(os.environ), patched_environment)
            self.assertEqual(json.loads(output.getvalue())["environment"]["ENABLE_SIGNUP"], "false")

        invalid = self.root / "bad.json"
        invalid.write_text('{"secret_marker":"never echo"}', encoding="utf-8")
        refusal = subprocess.run(
            [sys.executable, str(SCRIPT), "--manifest", str(invalid)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(refusal.returncode, 2)
        self.assertEqual(refusal.stderr, "")
        refusal_json = json.loads(refusal.stdout)
        self.assertEqual(refusal_json["refusal"], "manifest_schema_mismatch")
        self.assertTrue(refusal_json["partial"])
        self.assertFalse(refusal_json["cutover"])
        self.assertNotIn("secret_marker", refusal.stdout)
        self.assertEqual(dict(os.environ), environment_before)

        arguments = subprocess.run(
            [sys.executable, str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(arguments.returncode, 2)
        self.assertEqual(json.loads(arguments.stdout)["refusal"], "invalid_cli_arguments")
        self.assertEqual(arguments.stderr, "")

    def test_admin_get_config_projects_existing_login_form_boolean_only(self):
        tree = ast.parse(ROUTE_SOURCE.read_text(encoding="utf-8"))
        get_route = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_admin_config"
        )
        get_route.decorator_list = []
        get_route.returns = None
        for arg in (*get_route.args.posonlyargs, *get_route.args.args, *get_route.args.kwonlyargs):
            arg.annotation = None

        post_route = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_admin_config"
        )
        post_attributes = {node.attr for node in ast.walk(post_route) if isinstance(node, ast.Attribute)}
        self.assertNotIn("ENABLE_LOGIN_FORM", post_attributes)

        for enabled in (False, True):
            config = types.SimpleNamespace(
                SHOW_ADMIN_DETAILS=False,
                WEBUI_URL="https://community.invalid",
                ENABLE_SIGNUP=False,
                ENABLE_LOGIN_FORM=enabled,
                ENABLE_API_KEY=False,
                ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False,
                API_KEY_ALLOWED_ENDPOINTS="",
                DEFAULT_USER_ROLE="user",
                JWT_EXPIRES_IN="4w",
                ENABLE_COMMUNITY_SHARING=False,
                ENABLE_MESSAGE_RATING=False,
                ENABLE_CHANNELS=False,
                ENABLE_USER_WEBHOOKS=False,
            )
            namespace = {
                "Depends": lambda dependency: dependency,
                "get_admin_user": object(),
            }
            exec(compile(ast.Module(body=[get_route], type_ignores=[]), str(ROUTE_SOURCE), "exec"), namespace)
            request = types.SimpleNamespace(
                app=types.SimpleNamespace(state=types.SimpleNamespace(config=config))
            )
            result = asyncio.run(namespace["get_admin_config"](request, user=object()))
            self.assertIs(result["ENABLE_LOGIN_FORM"], enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
