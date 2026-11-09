# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused source contracts for measured provider proxy aliases."""

import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"
OPENAI = ROOT / "backend/open_webui/routers/openai.py"
OLLAMA = ROOT / "backend/open_webui/routers/ollama.py"


def functions(path):
    tree = ast.parse(path.read_text())
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def decorators(node):
    return {ast.unparse(item) for item in node.decorator_list}


class ProviderProxyAliasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = functions(MAIN)
        cls.openai = functions(OPENAI)
        cls.ollama = functions(OLLAMA)

    def test_global_alias_pairs_share_handlers(self):
        expected = {
            "embeddings": {"app.post('/api/embeddings')", "app.post('/api/v1/embeddings')"},
            "generate_messages": {"app.post('/api/message')", "app.post('/api/v1/messages')"},
            "count_message_tokens": {
                "app.post('/api/message/count_tokens')",
                "app.post('/api/v1/messages/count_tokens')",
            },
        }
        for name, routes in expected.items():
            self.assertEqual(routes, decorators(self.main[name]))

    def test_ollama_alias_pairs_share_access_controlled_proxy(self):
        expected = {
            "generate_openai_embeddings": {
                "router.post('/v1/embeddings')",
                "router.post('/v1/embeddings/{url_idx}')",
            },
            "generate_anthropic_messages": {
                "router.post('/v1/messages')",
                "router.post('/v1/messages/{url_idx}')",
            },
            "generate_responses": {
                "router.post('/v1/responses')",
                "router.post('/v1/responses/{url_idx}')",
            },
        }
        for name, routes in expected.items():
            self.assertEqual(routes, decorators(self.ollama[name]))
            self.assertIn("proxy_openai_compatible_json", ast.unparse(self.ollama[name]))

    def test_openai_response_route_uses_verified_user_and_shared_proxy(self):
        route = self.openai["responses"]
        self.assertEqual({"router.post('/responses')"}, decorators(route))
        self.assertIn("get_verified_user", ast.unparse(route.args))
        self.assertIn("proxy_openai_json", ast.unparse(route))

    def test_proxy_helpers_enforce_model_access(self):
        self.assertIn("has_access", ast.unparse(self.openai["proxy_openai_json"]))
        self.assertIn(
            "authorize_openai_compatible_model",
            ast.unparse(self.ollama["proxy_openai_compatible_json"]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
