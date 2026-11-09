# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused source contracts for the measured timezone compatibility route."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTHS = ROOT / "backend/open_webui/routers/auths.py"


class TimezoneCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(AUTHS.read_text())
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_timezone_form_requires_one_string_and_ignores_additive_fields(self):
        form = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "UpdateTimezoneForm"
        )
        assignment = next(
            node for node in form.body if isinstance(node, ast.AnnAssign)
        )
        self.assertEqual(assignment.target.id, "timezone")
        self.assertEqual(ast.unparse(assignment.annotation), "str")

    def test_timezone_update_is_authenticated_and_preserves_other_settings(self):
        function = self.functions["update_timezone"]
        self.assertIn("get_verified_user", ast.unparse(function.args))
        source = ast.unparse(function)
        self.assertIn("update_user_settings_by_id", source)
        self.assertIn("{'timezone': form_data.timezone}", source)
        self.assertNotIn("update_user_by_id", source)

    def test_measured_route_and_success_shape_are_stable(self):
        function = self.functions["update_timezone"]
        decorator = ast.unparse(function.decorator_list[0])
        self.assertIn("router.post('/update/timezone')", decorator)
        self.assertIn("{'status': True}", ast.unparse(function))


if __name__ == "__main__":
    unittest.main(verbosity=2)
