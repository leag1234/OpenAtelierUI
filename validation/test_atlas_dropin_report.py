#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused regression checks for the reported unchanged Atlas integration contracts."""

import ast
import asyncio
import base64
import binascii
from pathlib import Path
import tempfile
import types
import unittest
from urllib.parse import unquote

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"
MODELS = ROOT / "backend/open_webui/routers/models.py"
USERS = ROOT / "backend/open_webui/routers/users.py"
NOTES = ROOT / "backend/open_webui/routers/notes.py"
SRC = ROOT / "src"


def extracted_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    if {node.name for node in selected} != set(names):
        raise AssertionError("expected source functions were not found")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class Store:
    model = None

    @classmethod
    def get_model_by_id(cls, _model_id):
        return cls.model


class AtlasDropInReportTests(unittest.TestCase):
    def test_container_keeps_the_legacy_atlas_bootstrap_entrypoint(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        wrapper = ROOT / "packaging" / "start-with-atlas.sh"
        self.assertTrue(wrapper.is_file())
        self.assertIn(
            "./packaging/start-with-atlas.sh /opt/openatelier/start-with-atlas.sh",
            dockerfile,
        )
        source = wrapper.read_text()
        self.assertIn("open_webui.utils.async_model_bootstrap", source)
        self.assertIn("exec bash /app/backend/start.sh", source)

    def test_default_feature_activation_matches_existing_integration_inputs(self):
        ns = extracted_functions(
            MAIN,
            {"notes_compat_enabled", "terminal_compat_enabled", "spa_fallback_allowed"},
            {},
        )
        self.assertTrue(ns["notes_compat_enabled"]({}))
        self.assertFalse(ns["notes_compat_enabled"]({"OPENATELIER_PRIVATE_NOTES_COMPAT": "0"}))
        self.assertTrue(ns["terminal_compat_enabled"]({"TERMINAL_SERVER_CONNECTIONS": "[]"}))
        self.assertFalse(ns["terminal_compat_enabled"]({}))
        self.assertFalse(ns["spa_fallback_allowed"]("api/v1/unknown"))
        self.assertTrue(ns["spa_fallback_allowed"]("c/synthetic-chat"))
        self.assertFalse(ns["spa_fallback_allowed"]("_app/example.js"))
        main_source = MAIN.read_text(encoding="utf-8")
        self.assertIn("if NOTES_COMPAT_ENABLED:", main_source)
        self.assertIn("if TERMINAL_COMPAT_ENABLED:", main_source)
        self.assertIn("if not spa_fallback_allowed(path):", main_source)
        self.assertIn('.get("OPENATELIER_PRIVATE_NOTES_COMPAT", "1") != "0"', NOTES.read_text(encoding="utf-8"))

    def model_routes(self, static_dir):
        status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_404_NOT_FOUND=404)
        errors = types.SimpleNamespace(
            NOT_FOUND="not found", ACCESS_PROHIBITED="access prohibited"
        )
        return extracted_functions(
            MODELS,
            {"get_model_by_id", "get_model_profile_image"},
            {
                "Models": Store,
                "Depends": lambda dependency: dependency,
                "get_verified_user": lambda: None,
                "HTTPException": HTTPException,
                "status": status,
                "ERROR_MESSAGES": errors,
                "has_access": lambda *_args: False,
                "Path": Path,
                "unquote": unquote,
                "STATIC_DIR": static_dir,
                "FileResponse": FileResponse,
                "RedirectResponse": RedirectResponse,
                "Response": Response,
                "base64": base64,
                "binascii": binascii,
            },
        )

    def test_missing_model_is_404_but_existing_private_model_is_401(self):
        routes = self.model_routes("/tmp")
        user = types.SimpleNamespace(id="caller", role="admin")
        Store.model = None
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(routes["get_model_by_id"]("missing", user))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "not found")

        Store.model = types.SimpleNamespace(
            id="private", user_id="owner", access_control={},
            meta=types.SimpleNamespace(profile_image_url="/static/favicon.png"),
        )
        user = types.SimpleNamespace(id="caller", role="user")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(routes["get_model_profile_image"]("private", user))
        self.assertEqual(caught.exception.status_code, 401)

    def test_model_profile_image_serves_bounded_local_static_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "atlas" / "avatar.png"
            image.parent.mkdir()
            image.write_bytes(b"synthetic-png")
            routes = self.model_routes(directory)
            Store.model = types.SimpleNamespace(
                id="atlas", user_id="owner", access_control=None,
                meta=types.SimpleNamespace(profile_image_url="/static/atlas/avatar.png"),
            )
            user = types.SimpleNamespace(id="owner", role="user")
            response = asyncio.run(routes["get_model_profile_image"]("atlas", user))
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), image.resolve())

    def test_admin_user_list_alias_precedes_dynamic_user_route(self):
        tree = ast.parse(USERS.read_text(encoding="utf-8"))
        get_users = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_users")
        paths = []
        for decorator in get_users.decorator_list:
            if isinstance(decorator, ast.Call) and decorator.args:
                value = decorator.args[0]
                if isinstance(value, ast.Constant):
                    paths.append(value.value)
        self.assertEqual(set(paths), {"/", "/all"})
        self.assertIn("user=Depends(get_admin_user)", ast.unparse(get_users))

    def test_all_reported_dom_hooks_exist_once(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SRC.rglob("*.svelte")
        )
        for element_id in (
            "input-menu-button",
            "message-input-container",
            "stop-response-button",
            "main-content",
            "sidebar-webui-name",
            "pinned-menu-items-list",
            "tab-general",
        ):
            self.assertEqual(source.count(f'id="{element_id}"'), 1, element_id)


if __name__ == "__main__":
    unittest.main()
