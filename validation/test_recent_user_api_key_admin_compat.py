# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for recent target-user API-key administration routes."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
USERS = ROOT / "backend/open_webui/routers/users.py"


def declared_routes():
    tree = ast.parse(USERS.read_text(encoding="utf-8"), filename=str(USERS))
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if decorator.func.attr not in {"post", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.add((decorator.func.attr.upper(), decorator.args[0].value))
    return routes


class RecentUserApiKeyAdminCompatibilityTests(unittest.TestCase):
    def test_both_target_user_routes_are_declared(self):
        self.assertIn(("POST", "/{user_id}/api_key/rotate"), declared_routes())
        self.assertIn(("DELETE", "/{user_id}/api_key"), declared_routes())

    def test_routes_require_administrator_and_validate_target(self):
        source = USERS.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("user=Depends(get_admin_user)"), 2)
        self.assertIn("target = Users.get_user_by_id(user_id)", source)
        self.assertIn("status_code=404, detail=ERROR_MESSAGES.NOT_FOUND", source)

    def test_rotation_obeys_global_and_target_permission_controls(self):
        source = USERS.read_text(encoding="utf-8")
        self.assertIn("request.app.state.config.ENABLE_API_KEY", source)
        self.assertIn('permissions.get("features", {}).get(', source)
        self.assertIn("ERROR_MESSAGES.API_KEY_CREATION_NOT_ALLOWED", source)

    def test_key_material_is_only_returned_from_rotation(self):
        source = USERS.read_text(encoding="utf-8")
        self.assertIn('return {"api_key": api_key}', source)
        self.assertIn("Users.update_user_api_key_by_id(target.id, None)", source)


if __name__ == "__main__":
    unittest.main()
