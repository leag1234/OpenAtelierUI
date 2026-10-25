# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted tests for the old tool-server verify route behavior."""

import ast
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "backend/open_webui/routers/configs.py"
ADMIN_DEPENDENCY = object()


class TestHTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def load_handler(loader):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_tool_servers_config"
    )
    # Execute the function body without registering a FastAPI route in this
    # dependency-free source-bound unit test.
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "Depends": lambda dependency: ("dependency", dependency),
        "get_admin_user": ADMIN_DEPENDENCY,
        "Request": object,
        "ToolServerConnection": object,
        "get_tool_server_data": loader,
        "HTTPException": TestHTTPException,
    }
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["verify_tool_servers_config"]


class VerifyToolServerResponseTests(unittest.IsolatedAsyncioTestCase):
    def make_request(self, token="session-token"):
        return SimpleNamespace(state=SimpleNamespace(token=SimpleNamespace(credentials=token)))

    def make_form(self, auth_type=None, key=None, url="http://127.0.0.1:18081", path="openapi.json"):
        return SimpleNamespace(auth_type=auth_type, key=key, url=url, path=path)

    async def test_admin_verify_returns_the_exact_openapi_document(self):
        schema = {
            "openapi": "3.0.3",
            "info": {"title": "Synthetic OpenAPI tool fixture", "version": "1.0"},
            "paths": {"/echo": {"post": {"operationId": "synthetic_echo"}}},
            "components": {"schemas": {"EchoRequest": {"type": "object"}}},
        }
        envelope = {"openapi": schema, "info": schema["info"], "specs": [{"name": "synthetic_echo"}]}
        calls = []

        async def loader(token, url):
            calls.append((token, url))
            return envelope

        handler = load_handler(loader)
        result = await handler(self.make_request(), self.make_form())
        self.assertIs(result, schema)
        self.assertEqual(result, schema)
        self.assertNotEqual(result, envelope)
        self.assertEqual(calls, [(None, "http://127.0.0.1:18081/openapi.json")])

    async def test_bearer_auth_selection_and_literal_url_join_remain_unchanged(self):
        calls = []

        async def loader(token, url):
            calls.append((token, url))
            return {"openapi": {"paths": {}}}

        handler = load_handler(loader)
        result = await handler(
            self.make_request(), self.make_form(auth_type="bearer", key="synthetic-token", url="http://127.0.0.1:18081/", path="openapi.json")
        )
        self.assertEqual(result, {"paths": {}})
        self.assertEqual(calls, [("synthetic-token", "http://127.0.0.1:18081//openapi.json")])

    async def test_session_auth_selection_remains_unchanged(self):
        calls = []

        async def loader(token, url):
            calls.append((token, url))
            return {"openapi": {"info": {"title": "fixture"}}}

        handler = load_handler(loader)
        result = await handler(
            self.make_request("in-memory-session"), self.make_form(auth_type="session")
        )
        self.assertEqual(result, {"info": {"title": "fixture"}})
        self.assertEqual(calls, [("in-memory-session", "http://127.0.0.1:18081/openapi.json")])

    async def test_loader_failure_keeps_existing_http_error_mapping(self):
        async def loader(_token, _url):
            raise RuntimeError("synthetic fetch failure")

        handler = load_handler(loader)
        with self.assertRaises(TestHTTPException) as raised:
            await handler(self.make_request(), self.make_form())
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            "Failed to connect to the tool server: synthetic fetch failure",
        )

    def test_admin_dependency_declaration_is_preserved(self):
        async def loader(_token, _url):
            return {"openapi": {}}

        handler = load_handler(loader)
        self.assertEqual(inspect.signature(handler).parameters["user"].default, ("dependency", ADMIN_DEPENDENCY))
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "verify_tool_servers_config"
        )
        user_parameter = next(arg for arg in function.args.args if arg.arg == "user")
        self.assertEqual(ast.unparse(function.args.defaults[-1]), "Depends(get_admin_user)")
        self.assertEqual(user_parameter.arg, "user")


if __name__ == "__main__":
    unittest.main(verbosity=2)
