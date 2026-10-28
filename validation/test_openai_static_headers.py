#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Bounded static OpenAI header validation and dispatch controls."""

import ast
import asyncio
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "backend/open_webui/utils/openai_static_headers.py"
SPEC = importlib.util.spec_from_file_location("static_headers_tested", HELPER)
static_headers = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(static_headers)


class Response:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return {"data": []}


class Session:
    captured = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, *, headers):
        type(self).captured = (url, dict(headers))
        return Response()


class StaticHeaderTests(unittest.TestCase):
    def test_absence_is_unchanged_and_selected_index_isolated(self):
        base = {"Authorization": "Bearer built-in", "Content-Type": "application/json"}
        self.assertEqual(static_headers.merge_static_headers(base, None), base)
        configs = {
            "0": {"headers": {"Origin": "http://localhost:3000"}},
            "1": {"headers": {"Origin": "https://second.invalid"}},
        }
        first = static_headers.configured_static_headers(configs, 0, "http://one.invalid")
        second = static_headers.configured_static_headers(configs, 1, "http://two.invalid")
        self.assertEqual(first, {"Origin": "http://localhost:3000"})
        self.assertEqual(second, {"Origin": "https://second.invalid"})
        legacy = static_headers.configured_static_headers(
            {"http://one.invalid": {"headers": {"X-Synthetic": "legacy"}}},
            0,
            "http://one.invalid",
        )
        self.assertEqual(legacy, {"X-Synthetic": "legacy"})

    def test_case_insensitive_operator_override_and_actual_catalog_get(self):
        base = {"Authorization": "Bearer old", "Content-Type": "application/json"}
        configured = {"authorization": "Bearer operator", "Origin": "http://localhost:3000"}
        merged = static_headers.merge_static_headers(base, configured)
        self.assertEqual(merged, {"Content-Type": "application/json", "authorization": "Bearer operator", "Origin": "http://localhost:3000"})
        first = static_headers.configured_static_headers(
            {"0": {"headers": {"Origin": "http://localhost:3000"}}},
            0,
            "http://one.invalid",
        )

        source = (ROOT / "backend/open_webui/routers/openai.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        send = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "send_get_request")
        class Aiohttp:
            ClientTimeout = staticmethod(lambda **kwargs: None)
            ClientSession = Session
        namespace = {
            "aiohttp": Aiohttp,
            "AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST": 1,
            "ENABLE_FORWARD_USER_INFO_HEADERS": False,
            "merge_static_headers": static_headers.merge_static_headers,
            "UserModel": object,
            "log": type("Log", (), {"error": staticmethod(lambda *args: None)})(),
        }
        exec(compile(ast.Module(body=[send], type_ignores=[]), "openai-send-get", "exec"), namespace)
        asyncio.run(namespace["send_get_request"](
            "http://one.invalid/models", "key", static_headers=first
        ))
        self.assertEqual(Session.captured, (
            "http://one.invalid/models",
            {"Authorization": "Bearer key", "Origin": "http://localhost:3000"},
        ))

    def test_router_catalog_chat_and_proxy_paths_resolve_static_config(self):
        source = (ROOT / "backend/open_webui/routers/openai.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        catalog = ast.unparse(functions["get_all_models_responses"])
        single_catalog = ast.unparse(functions["get_models"])
        chat = ast.unparse(functions["generate_chat_completion"])
        proxy = ast.unparse(functions["proxy"])
        self.assertIn("configured_static_headers", catalog)
        self.assertIn("configured_static_headers", single_catalog)
        self.assertIn("merge_static_headers", chat)
        self.assertIn("merge_static_headers", proxy)

    def test_invalid_headers_fail_closed(self):
        invalid = (
            ["not-a-map"],
            {"bad name": "x"},
            {"X-Test": 3},
            {"X-Test": "line\nbreak"},
            {"X-Test": "nul\x00value"},
            {"X-Test": "control\x01"},
            {"X-Test": "control\x85"},
            {"X-Test": "{{ user }}"},
            {"X-Test": "${USER}"},
            {"Origin": "a", "origin": "b"},
            {"X-Test": "x" * (static_headers.MAX_HEADER_VALUE_BYTES + 1)},
            {f"X-{index}": "v" for index in range(static_headers.MAX_HEADER_COUNT + 1)},
            {"X-Test": "x" * static_headers.MAX_HEADER_TOTAL_BYTES},
        )
        for case in invalid:
            with self.subTest(case=type(case).__name__):
                with self.assertRaises(ValueError):
                    static_headers.validate_static_headers(case)


if __name__ == "__main__":
    unittest.main(verbosity=2)
