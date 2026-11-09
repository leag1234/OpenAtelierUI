# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused source contracts for measured readiness and usage compatibility."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"


class RuntimeStatusCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(MAIN.read_text())
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_readiness_contract_checks_the_database(self):
        function = self.functions["readiness_check"]
        source = ast.unparse(function)
        self.assertIn("app.get('/ready'", ast.unparse(function.decorator_list[0]))
        self.assertIn("Session.execute(text('SELECT 1;')).all()", source)
        self.assertIn("{'status': True}", source)

    def test_usage_contract_is_authenticated_and_reports_live_models(self):
        function = self.functions["get_current_usage"]
        self.assertIn("get_verified_user", ast.unparse(function.args))
        source = ast.unparse(function)
        self.assertIn("app.get('/api/usage'", ast.unparse(function.decorator_list[0]))
        self.assertIn("list(USAGE_POOL.keys())", source)
        self.assertIn("Users.get_num_users()", source)

    def test_operation_ids_and_documentation_match_measured_references(self):
        readiness = self.functions["readiness_check"]
        usage = self.functions["get_current_usage"]
        readiness_route = ast.unparse(readiness.decorator_list[0])
        usage_route = ast.unparse(usage.decorator_list[0])
        self.assertIn("summary='Readiness Check'", readiness_route)
        self.assertIn("ready to accept traffic", readiness_route)
        self.assertIn("summary='Get Current Usage'", usage_route)
        self.assertIn("experimental endpoint", usage_route)


if __name__ == "__main__":
    unittest.main(verbosity=2)
