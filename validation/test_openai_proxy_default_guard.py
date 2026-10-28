#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-isolated checks for the generic OpenAI passthrough guard."""

import ast
import asyncio
import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/openai.py"
STATIC_HEADERS = ROOT / "backend/open_webui/utils/openai_static_headers.py"
spec = importlib.util.spec_from_file_location("test_static_headers", STATIC_HEADERS)
static_headers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(static_headers)


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}

    def raise_for_status(self):
        pass

    async def json(self):
        return {"result": "synthetic"}

    def close(self):
        pass


class Session:
    calls = []

    def __init__(self, **kwargs):
        self.closed = False

    async def request(self, **kwargs):
        type(self).calls.append(kwargs)
        return Response()

    async def close(self):
        self.closed = True


class Aiohttp:
    ClientSession = Session


def extracted_proxy():
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
    nodes = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    namespace = {
        "os": os,
        "router": type("Router", (), {"api_route": lambda *args, **kwargs: lambda fn: fn})(),
        "Request": object,
        "Depends": lambda value: value,
        "get_verified_user": object(),
        "aiohttp": Aiohttp,
        "HTTPException": HTTPError,
        "ENABLE_FORWARD_USER_INFO_HEADERS": False,
        "merge_static_headers": static_headers.merge_static_headers,
        "configured_static_headers": static_headers.configured_static_headers,
        "BackgroundTask": object,
        "cleanup_response": object(),
        "log": type("Log", (), {"exception": staticmethod(lambda *args: None), "error": staticmethod(lambda *args: None)})(),
    }
    exec(compile(ast.Module(body=[nodes["proxy"]], type_ignores=[]), str(ROUTER), "exec"), namespace)
    return namespace["proxy"], nodes


class DisabledRequest:
    @property
    def app(self):
        raise AssertionError("disabled proxy read application credentials")

    async def body(self):
        raise AssertionError("disabled proxy read the request body")


class EnabledRequest:
    method = "GET"

    def __init__(self):
        config = type("Config", (), {
            "OPENAI_API_BASE_URLS": ["https://provider.invalid/v1"],
            "OPENAI_API_KEYS": ["synthetic-key"],
            "OPENAI_API_CONFIGS": {"0": {"headers": {"Origin": "https://client.invalid"}}},
        })()
        self.app = type("App", (), {"state": type("State", (), {"config": config})()})()

    async def body(self):
        return b"{}"


class ProxyDefaultGuardTests(unittest.TestCase):
    def setUp(self):
        self.proxy, self.nodes = extracted_proxy()
        Session.calls = []
        os.environ.pop("ENABLE_OPENAI_API_PASSTHROUGH", None)

    def tearDown(self):
        os.environ.pop("ENABLE_OPENAI_API_PASSTHROUGH", None)

    def test_default_refuses_before_body_or_provider_configuration(self):
        with self.assertRaises(HTTPError) as caught:
            asyncio.run(self.proxy("models", DisabledRequest(), user=object()))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.detail, "Direct API passthrough is disabled")
        self.assertEqual(Session.calls, [])

    def test_explicit_true_enables_existing_scoped_proxy(self):
        os.environ["ENABLE_OPENAI_API_PASSTHROUGH"] = "True"
        result = asyncio.run(self.proxy("models", EnabledRequest(), user=object()))
        self.assertEqual(result, {"result": "synthetic"})
        self.assertEqual(len(Session.calls), 1)
        sent = Session.calls[0]
        self.assertEqual(sent["url"], "https://provider.invalid/v1/models")
        self.assertEqual(sent["headers"]["Origin"], "https://client.invalid")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer synthetic-key")

    def test_dedicated_catalog_and_chat_do_not_depend_on_proxy_switch(self):
        names = {
            node.name: node
            for node in ast.parse(ROUTER.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("get_all_models_responses", "get_models", "generate_chat_completion"):
            source = ast.unparse(names[name])
            self.assertNotIn("ENABLE_OPENAI_API_PASSTHROUGH", source)
        self.assertIn("configured_static_headers", ast.unparse(names["get_all_models_responses"]))
        self.assertIn("merge_static_headers", ast.unparse(names["generate_chat_completion"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
