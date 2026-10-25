"""Source-extracted checks for the bounded named OpenAPI server adapter."""

import ast
import asyncio
import inspect
import json
import logging
import re
import unittest
from functools import partial, update_wrapper
from pathlib import Path
from typing import Awaitable, Callable, Optional


ROOT = Path(__file__).resolve().parents[1]
UTILS_SOURCE = ROOT / "backend/open_webui/utils/tools.py"


def _load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    found = {node.name for node in nodes}
    if found != set(names):
        raise AssertionError(f"Missing source functions: {sorted(set(names) - found)}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class NamedOpenAPIServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.namespace = {
            "re": re,
            "json": json,
            "hashlib": __import__("hashlib"),
            "Optional": Optional,
            "Callable": Callable,
            "Awaitable": Awaitable,
            "inspect": inspect,
            "partial": partial,
            "update_wrapper": update_wrapper,
            "log": logging.getLogger("named-openapi-tests"),
            "_NAMED_SERVER_ID": re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"),
            "Request": object,
            "UserModel": object,
            "copy": __import__("copy"),
            "load_tool_module_by_id": lambda _tool_id: (None, None),
        }
        _load_functions(
            UTILS_SOURCE,
            {
                "_named_server_config",
                "_tool_server_connection_binding",
                "named_tool_server_catalog_ids",
                "resolve_tool_server_reference",
                "is_reserved_named_server_reference",
                "get_async_tool_function_and_apply_extra_params",
                "get_tools",
            },
            self.namespace,
        )
        self.local_lookup_calls = []
        self.server_calls = []

        class ToolStore:
            @staticmethod
            def get_tool_by_id(tool_id):
                self.local_lookup_calls.append(tool_id)
                return type("Tool", (), {"specs": []})() if self.local_collision else None

        self.namespace["Tools"] = ToolStore

        async def execute_tool_server(**kwargs):
            self.server_calls.append(kwargs)
            return {"ok": True}

        self.namespace["execute_tool_server"] = execute_tool_server
        self.local_collision = False

    def named_connection(self, server_id="synthetic_openapi", **overrides):
        connection = {
            "type": "openapi",
            "spec_type": "url",
            "info": {"id": server_id, "title": "Synthetic server"},
            "url": "https://example.invalid/openapi.json",
            "config": {"enable": True},
        }
        connection.update(overrides)
        return connection

    def cache_entry(self, index, connection, *, specs=None):
        return {
            "idx": index,
            "url": connection.get("url"),
            "connection_binding": self.namespace["_tool_server_connection_binding"](connection),
            "specs": specs if specs is not None else [{"name": "echo", "parameters": {"properties": {}}}],
            "openapi": {"info": {"title": "Synthetic server"}},
        }

    def invoke(self, connections, servers, requested_id, role="admin"):
        config = type("Config", (), {"TOOL_SERVER_CONNECTIONS": connections})()
        state = type("State", (), {
            "config": config,
            "TOOL_SERVERS": servers,
            "TOOLS": {},
        })()
        app = type("App", (), {"state": state})()
        request = type("RequestValue", (), {"app": app, "state": type("RequestState", (), {"token": None})()})()
        user = type("User", (), {"role": role, "id": "user-1"})()
        return self.namespace["get_tools"](request, [requested_id], user, {"__user__": {}})

    async def test_admin_gets_canonical_named_id_and_executes_cached_spec(self):
        connection = self.named_connection()
        server = self.cache_entry(0, connection)
        catalog, named_indices = self.namespace["named_tool_server_catalog_ids"]([connection], [server])
        self.assertEqual(catalog, {0: "server:synthetic_openapi"})
        self.assertEqual(named_indices, {0})
        tools = self.invoke([connection], [server], "server:synthetic_openapi", "admin")
        self.assertEqual(set(tools), {"echo"})
        await tools["echo"]["callable"](value="hello")
        self.assertEqual(len(self.server_calls), 1)
        self.assertEqual(self.server_calls[0]["name"], "echo")

    async def test_non_admin_and_numeric_alias_never_construct_callable_or_local_fallback(self):
        connection = self.named_connection()
        server = self.cache_entry(0, connection)
        self.local_collision = True
        for role, tool_id in (
            ("user", "server:synthetic_openapi"),
            ("user", "server:0"),
        ):
            self.assertEqual(self.invoke([connection], [server], tool_id, role), {})
        self.assertEqual(self.server_calls, [])
        self.assertEqual(self.local_lookup_calls, [])

        # An administrator cannot choose a local plugin that collides with the
        # canonical server ID; the ambiguity is rejected before callable creation.
        self.assertEqual(self.invoke([connection], [server], "server:synthetic_openapi", "admin"), {})
        self.assertEqual(self.invoke([connection], [server], "server:00", "admin"), {})
        self.assertEqual(self.local_lookup_calls, ["server:synthetic_openapi", "server:00"])
        self.assertEqual(self.server_calls, [])

    async def test_disabled_stale_duplicate_and_malformed_named_configs_fail_closed(self):
        good = self.named_connection()
        disabled = self.named_connection(config={"enable": False})
        stale = self.named_connection()
        changed = dict(stale, url="https://changed.invalid/spec.json")
        duplicate_a = self.named_connection("duplicate")
        duplicate_b = self.named_connection("duplicate")
        malformed = self.named_connection("bad/id")
        scenarios = [
            ([disabled], [self.cache_entry(0, disabled)], "server:synthetic_openapi"),
            ([stale], [self.cache_entry(0, changed)], "server:synthetic_openapi"),
            ([duplicate_a, duplicate_b], [self.cache_entry(0, duplicate_a), self.cache_entry(1, duplicate_b)], "server:duplicate"),
            ([malformed], [self.cache_entry(0, malformed)], "server:bad/id"),
        ]
        for connections, servers, requested_id in scenarios:
            with self.subTest(requested_id=requested_id, connections=len(connections)):
                self.assertEqual(self.invoke(connections, servers, requested_id, "admin"), {})
        self.assertEqual(self.invoke([good], [self.cache_entry(0, good)], "server:unknown", "admin"), {})
        self.assertEqual(self.server_calls, [])

    async def test_legacy_numeric_server_id_and_local_plugin_path_remain_available(self):
        connection = {
            "type": "openapi",
            "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        server = {
            "idx": 0,
            "url": connection["url"],
            "specs": [{"name": "legacy_echo", "parameters": {"properties": {}}}],
            "openapi": {"info": {"title": "Legacy"}},
        }
        result = self.invoke([connection], [server], "server:0", "user")
        self.assertEqual(set(result), {"legacy_echo"})
        await result["legacy_echo"]["callable"]()
        self.assertEqual(self.server_calls[-1]["name"], "legacy_echo")

        self.local_collision = True
        self.assertEqual(self.invoke([connection], [server], "server:local_tool", "user"), {})
        self.assertEqual(self.local_lookup_calls[-1], "server:local_tool")

    async def test_distinct_sparse_config_indices_keep_the_original_connection_index(self):
        legacy = {"type": "openapi", "url": "https://example.invalid/old.json", "config": {"enable": True}}
        named = self.named_connection("named")
        servers = [self.cache_entry(1, named)]
        catalog, _ = self.namespace["named_tool_server_catalog_ids"]([legacy, named], servers)
        self.assertEqual(catalog, {1: "server:named"})
        self.assertIs(self.namespace["resolve_tool_server_reference"]("server:named", [legacy, named], servers, "admin")[0], named)


if __name__ == "__main__":
    unittest.main(verbosity=2)
