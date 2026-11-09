# SPDX-License-Identifier: BSD-3-Clause
"""Focused checks for the measured administrator configuration family."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/configs.py"
CONFIG = ROOT / "backend/open_webui/config.py"
MAIN = ROOT / "backend/open_webui/main.py"


class ConfigurationCompatibilityTests(unittest.TestCase):
    def test_namespace_flattens_only_the_requested_prefix(self):
        tree = ast.parse(ROUTER.read_text())
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_flatten_config"
        )
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROUTER), "exec"), namespace)
        flattened = namespace["_flatten_config"](
            {"direct": {"enable": False}, "subagents": {"max_async": 4}, "plain": 3}
        )
        self.assertEqual(
            flattened,
            {"direct.enable": False, "subagents.max_async": 4, "plain": 3},
        )

    def test_six_common_operations_and_role_dependencies_are_declared(self):
        tree = ast.parse(ROUTER.read_text())
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
        }
        admin = {
            "get_connections_config", "set_connections_config",
            "get_subagents_config", "set_subagents_config", "get_config_namespace",
        }
        self.assertTrue(admin <= functions.keys())
        for name in admin:
            self.assertIn("get_admin_user", ast.unparse(functions[name].args))
        self.assertIn("get_verified_user", ast.unparse(functions["get_models_defaults"].args))

    def test_reference_defaults_and_persistent_paths_are_explicit(self):
        source = CONFIG.read_text()
        for fragment in (
            '"direct.base_models_cache"', '"direct.integrations.enable"',
            '"subagents.enable"', '"subagents.background_enabled"',
            '"subagents.max_concurrent"', '"subagents.max_async"',
            '"subagents.max_iterations"', '"subagents.max_output"',
            '"subagents.system_prompt"', '"models.defaults"',
        ):
            self.assertIn(fragment, source)
        self.assertIn('os.environ.get("ENABLE_DIRECT_CONNECTIONS", "False")', source)

    def test_runtime_registers_every_new_persistent_value(self):
        source = MAIN.read_text()
        for name in (
            "ENABLE_BASE_MODELS_CACHE", "ENABLE_DIRECT_INTEGRATIONS", "ENABLE_SUBAGENTS",
            "SUBAGENTS_BACKGROUND_ENABLED", "SUBAGENTS_MAX_CONCURRENT", "SUBAGENTS_MAX_ASYNC",
            "SUBAGENTS_MAX_ITERATIONS", "SUBAGENTS_MAX_OUTPUT", "SUBAGENTS_SYSTEM_PROMPT",
            "DEFAULT_MODEL_METADATA",
        ):
            self.assertIn(f"app.state.config.{name} = {name}", source)

    def test_baseline_connection_body_preserves_current_additive_default(self):
        source = ROUTER.read_text()
        self.assertIn("ENABLE_DIRECT_INTEGRATIONS: Optional[bool] = None", source)
        self.assertIn("if form_data.ENABLE_DIRECT_INTEGRATIONS is not None", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
