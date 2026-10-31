#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-bound controls for the explicit API-key feature permission."""

from __future__ import annotations

import ast
import asyncio
import pathlib
import types
import unittest

from fastapi import HTTPException, Request, status


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH_ROUTER = ROOT / "backend/open_webui/routers/auths.py"


def extract_generate_api_key(namespace):
    tree = ast.parse(AUTH_ROUTER.read_text(encoding="utf-8"), filename=str(AUTH_ROUTER))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_api_key"
    ]
    if len(functions) != 1:
        raise AssertionError("actual generate_api_key function was not found exactly once")
    function = functions[0]
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(AUTH_ROUTER), "exec"), namespace)
    return namespace["generate_api_key"]


class ApiKeyUserPermissionTests(unittest.TestCase):
    def run_route(self, *, role="user", permission="missing", enabled=True):
        effects = {"permission_checks": [], "created": 0, "stored": 0}

        class UsersStub:
            @staticmethod
            def update_user_api_key_by_id(_identity, _value):
                effects["stored"] += 1
                return True

        def has_permission(identity, key, configured):
            effects["permission_checks"].append((identity, key, configured))
            return configured["features"]["api_keys"]

        namespace = {
            "HTTPException": HTTPException,
            "Request": Request,
            "status": status,
            "Depends": lambda dependency: dependency,
            "get_current_user": object(),
            "ERROR_MESSAGES": types.SimpleNamespace(
                API_KEY_CREATION_NOT_ALLOWED="API key creation is not allowed in the environment."
            ),
            "has_permission": has_permission,
            "create_api_key": lambda: effects.__setitem__("created", effects["created"] + 1) or "synthetic-key",
            "Users": UsersStub,
        }
        route = extract_generate_api_key(namespace)
        permissions = {} if permission == "missing" else {"features": {"api_keys": permission}}
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(
                    config=types.SimpleNamespace(
                        ENABLE_API_KEY=enabled,
                        USER_PERMISSIONS=permissions,
                    )
                )
            )
        )
        user = types.SimpleNamespace(id="synthetic-member", role=role)
        try:
            result = asyncio.run(route(request, user))
        except HTTPException as error:
            return error, effects
        return result, effects

    def test_explicit_true_and_false_preserve_existing_global_gate(self):
        result, effects = self.run_route(permission=True)
        self.assertEqual(result, {"api_key": "synthetic-key"})
        self.assertEqual(effects["permission_checks"], [
            ("synthetic-member", "features.api_keys", {"features": {"api_keys": True}})
        ])
        self.assertEqual((effects["created"], effects["stored"]), (1, 1))

        denied, effects = self.run_route(permission=False)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual((effects["created"], effects["stored"]), (0, 0))

        globally_denied, effects = self.run_route(permission=True, enabled=False)
        self.assertEqual(globally_denied.status_code, 403)
        self.assertEqual(effects["permission_checks"], [])
        self.assertEqual((effects["created"], effects["stored"]), (0, 0))

    def test_missing_feature_preserves_legacy_but_malformed_explicit_values_deny(self):
        result, effects = self.run_route(permission="missing")
        self.assertEqual(result, {"api_key": "synthetic-key"})
        self.assertEqual(effects["permission_checks"], [])
        for value in (None, 0, 1, "true"):
            with self.subTest(value=value):
                denied, effects = self.run_route(permission=value)
                self.assertEqual(denied.status_code, 403)
                self.assertEqual((effects["created"], effects["stored"]), (0, 0))

    def test_admin_path_is_unchanged_and_does_not_consult_member_permissions(self):
        result, effects = self.run_route(role="admin", permission=False)
        self.assertEqual(result, {"api_key": "synthetic-key"})
        self.assertEqual(effects["permission_checks"], [])
        self.assertEqual((effects["created"], effects["stored"]), (1, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
