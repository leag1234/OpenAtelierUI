# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused source contracts for measured provider-free OAuth compatibility."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTHS = ROOT / "backend/open_webui/routers/auths.py"
CONFIGS = ROOT / "backend/open_webui/routers/configs.py"
MAIN = ROOT / "backend/open_webui/main.py"


def functions(path):
    tree = ast.parse(path.read_text())
    return tree, {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class OAuthCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.auths_tree, cls.auths = functions(AUTHS)
        cls.configs_tree, cls.configs = functions(CONFIGS)
        cls.main_tree, cls.main = functions(MAIN)

    def test_admin_config_routes_share_the_measured_model(self):
        get_route = self.auths["get_oauth_config"]
        post_route = self.auths["update_oauth_config"]
        self.assertIn("router.get('/admin/config/oauth'", ast.unparse(get_route.decorator_list[0]))
        self.assertIn("router.post('/admin/config/oauth'", ast.unparse(post_route.decorator_list[0]))
        self.assertIn("get_admin_user", ast.unparse(get_route.args))
        self.assertIn("get_admin_user", ast.unparse(post_route.args))
        self.assertIn("exclude_none=True", ast.unparse(post_route))

    def test_session_delete_and_disabled_exchange_fail_closed(self):
        delete = self.auths["delete_oauth_session_by_provider"]
        exchange = self.auths["token_exchange"]
        self.assertIn("get_verified_user", ast.unparse(delete.args))
        self.assertIn("No OAuth session found for this provider", ast.unparse(delete))
        self.assertNotIn("Depends", ast.unparse(exchange.args))
        self.assertIn("Token exchange is disabled", ast.unparse(exchange))

    def test_client_registration_requires_admin_and_has_no_network_call(self):
        register = self.configs["register_oauth_client"]
        source = ast.unparse(register)
        self.assertIn("get_admin_user", ast.unparse(register.args))
        self.assertIn("/.well-known/oauth-authorization-server", source)
        self.assertNotIn("aiohttp", source)
        self.assertNotIn("requests", source)

    def test_unconfigured_callback_routes_are_deterministic(self):
        for name in (
            "oauth_client_authorize",
            "oauth_client_callback",
            "oauth_login_callback",
            "oauth_backchannel_logout",
        ):
            self.assertIn("detail='Not Found'", ast.unparse(self.main[name]))
        self.assertIn("get_verified_user", ast.unparse(self.main["oauth_client_authorize"].args))


if __name__ == "__main__":
    unittest.main(verbosity=2)
