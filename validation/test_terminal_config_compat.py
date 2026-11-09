# SPDX-License-Identifier: BSD-3-Clause
"""Focused checks for the measured terminal-server configuration contract."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/configs.py"
CONFIG = ROOT / "backend/open_webui/config.py"
MAIN = ROOT / "backend/open_webui/main.py"


class TerminalConfigurationCompatibilityTests(unittest.TestCase):
    def test_six_common_operations_require_an_administrator(self):
        tree = ast.parse(ROUTER.read_text())
        names = {
            "get_terminal_servers_config",
            "set_terminal_servers_config",
            "verify_terminal_server_connection",
            "put_terminal_server_policy",
            "put_terminal_server_lifecycle",
            "refresh_terminal_server_terminals",
        }
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in names
        }
        self.assertEqual(set(functions), names)
        for function in functions.values():
            self.assertIn("get_admin_user", ast.unparse(function.args))

    def test_current_additive_connection_field_and_reference_defaults_exist(self):
        source = ROUTER.read_text()
        for fragment in (
            'path: Optional[str] = "/openapi.json"',
            'auth_type: Optional[str] = "bearer"',
            'enabled: Optional[bool] = True',
            'forward_cookies: bool = False',
            'WithJsonSchema({"type": "object", "additionalProperties": True})',
            'config: Optional[TerminalServerObject] = None',
            'policy_data: Optional[TerminalServerObject] = None',
            'lifecycle_data: Optional[TerminalServerObject] = None',
            'only_idle: bool = True',
            'reset: bool = False',
            'model_config = ConfigDict(extra="allow")',
        ):
            self.assertIn(fragment, source)

    def test_persistent_path_and_runtime_registration_are_explicit(self):
        self.assertIn('"terminal_server.connections"', CONFIG.read_text())
        self.assertIn(
            "app.state.config.TERMINAL_SERVER_CONNECTIONS = TERMINAL_SERVER_CONNECTIONS",
            MAIN.read_text(),
        )

    def test_measured_synthetic_paths_and_payloads_are_preserved(self):
        source = ROUTER.read_text()
        for fragment in (
            'f"p/{policy_id}/openapi.json"',
            '"api/v1/policies"',
            'f"api/v1/policies/{policy_id}"',
            'f"api/v1/policies/{policy_id}/lifecycle"',
            '"api/v1/terminals/refresh"',
            '"only_idle": form_data.only_idle',
            '"reset": form_data.reset',
        ):
            self.assertIn(fragment, source)

    def test_path_join_and_bearer_headers_match_the_observed_contract(self):
        tree = ast.parse(ROUTER.read_text())
        selected = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_terminal_server_url", "_terminal_server_headers"}
        ]
        namespace = {}
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTER), "exec"), namespace)
        self.assertEqual(
            namespace["_terminal_server_url"]("http://terminal.invalid/", "/api/v1/policies"),
            "http://terminal.invalid/api/v1/policies",
        )
        self.assertEqual(
            namespace["_terminal_server_headers"]("bearer", "synthetic", True),
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer synthetic",
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
