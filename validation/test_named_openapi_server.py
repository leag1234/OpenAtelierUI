"""Source-extracted checks for the bounded named OpenAPI server adapter."""

import ast
import asyncio
import inspect
import json
import logging
import re
import time
import unicodedata
from urllib.parse import urlsplit
from types import SimpleNamespace
import unittest
from functools import partial, update_wrapper
from pathlib import Path
from typing import Awaitable, Callable, Optional


ROOT = Path(__file__).resolve().parents[1]
UTILS_SOURCE = ROOT / "backend/open_webui/utils/tools.py"
ROUTER_SOURCE = ROOT / "backend/open_webui/routers/tools.py"
FUNCTIONS_SOURCE = ROOT / "backend/open_webui/functions.py"


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
                "named_tool_server_read_allowed",
                "resolve_tool_server_reference",
                "is_reserved_named_server_reference",
                "get_async_tool_function_and_apply_extra_params",
                "get_tools",
            },
            self.namespace,
        )
        self.catalog_fetcher = None
        self.local_lookup_calls = []
        self.server_calls = []
        self.executor_result = {"ok": True}

        class ToolStore:
            @staticmethod
            def get_tool_by_id(tool_id):
                self.local_lookup_calls.append(tool_id)
                return type("Tool", (), {"specs": []})() if self.local_collision else None

        self.namespace["Tools"] = ToolStore

        async def execute_tool_server(**kwargs):
            self.server_calls.append(kwargs)
            return self.executor_result

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

    def grant_connection(self, connection, principal_id):
        return dict(
            connection,
            config={
                **connection.get("config", {}),
                "access_grants": [
                    {
                        "principal_type": "user",
                        "principal_id": principal_id,
                        "permission": "read",
                    }
                ],
            },
        )

    def invoke(
        self,
        connections,
        servers,
        requested_id,
        role="admin",
        refuse_config=False,
        user_id="user-1",
        include_response_headers=False,
    ):
        if refuse_config:
            class Config:
                @property
                def TOOL_SERVER_CONNECTIONS(self):
                    raise AssertionError("local tool selection read server configuration")

            config = Config()
        else:
            config = type("Config", (), {"TOOL_SERVER_CONNECTIONS": connections})()
        state = type("State", (), {
            "config": config,
            "TOOL_SERVERS": servers,
            "TOOLS": {},
        })()
        app = type("App", (), {"state": state})()
        request = type("RequestValue", (), {"app": app, "state": type("RequestState", (), {"token": None})()})()
        user = type("User", (), {"role": role, "id": user_id})()
        return self.namespace["get_tools"](
            request,
            [requested_id],
            user,
            {"__user__": {}},
            include_response_headers=include_response_headers,
        )

    def load_catalog_route(self, tool_rows):
        class ResponseRecord:
            def __init__(self, **fields):
                self.__dict__.update(fields)

            def model_dump(self, exclude=None):
                fields = dict(self.__dict__)
                for key in exclude or ():
                    fields.pop(key, None)
                return fields

        async def get_tool_servers_data(connections):
            if self.catalog_fetcher is None:
                return None
            return await self.catalog_fetcher(connections)

        namespace = {
            "asyncio": asyncio,
            "unicodedata": unicodedata,
            "urlsplit": urlsplit,
            "router": SimpleNamespace(get=lambda *_args, **_kwargs: lambda function: function),
            "Request": object,
            "Depends": lambda dependency: dependency,
            "get_verified_user": lambda: None,
            "time": time,
            "Tools": type("CatalogTools", (), {"get_tools": staticmethod(lambda: list(tool_rows))}),
            "ToolUserResponse": ResponseRecord,
            "_named_server_config": self.namespace["_named_server_config"],
            "_tool_server_connection_binding": self.namespace["_tool_server_connection_binding"],
            "named_tool_server_catalog_ids": self.namespace["named_tool_server_catalog_ids"],
            "named_tool_server_read_allowed": self.namespace["named_tool_server_read_allowed"],
            "get_tool_servers_data": get_tool_servers_data,
            "has_access": lambda *_args, **_kwargs: False,
            "_tool_access_fields": lambda _tool, _user: {
                "access_grants": [{"legacy": True}],
                "write_access": False,
            },
        }
        _load_functions(
            ROUTER_SOURCE,
            {"_refresh_named_tool_server_catalog", "_valid_named_server_display_text", "get_tools"},
            namespace,
        )
        self.catalog_refresh = namespace["_refresh_named_tool_server_catalog"]
        return namespace["get_tools"]

    async def catalog(self, connection, server, user, local_rows=()):
        config = type("Config", (), {"TOOL_SERVER_CONNECTIONS": [connection]})()
        state = type("State", (), {"config": config, "TOOL_SERVERS": [server]})()
        request = type(
            "RequestValue",
            (),
            {"app": type("App", (), {"state": state})()},
        )()
        return await self.load_catalog_route(local_rows)(request, user)

    def user(self, user_id="user-1", role="user"):
        return type("User", (), {"role": role, "id": user_id})()

    def local_tool_record(self, tool_id="server:synthetic_openapi", user_id="user-1"):
        class LocalTool:
            def __init__(self):
                self.id = tool_id
                self.user_id = user_id
                self.name = "Local colliding tool"
                self.meta = {"description": "Local row"}
                self.access_control = {"read": {"user_ids": [user_id]}}
                self.created_at = 10
                self.updated_at = 11
                self.content = "local source"
                self.specs = [{"name": "local"}]

            def model_dump(self, exclude=None):
                fields = dict(self.__dict__)
                for key in exclude or ():
                    fields.pop(key, None)
                return fields

        return LocalTool()

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
        self.assertNotIn("include_response_headers", self.server_calls[0])

    async def test_named_opt_in_forwards_header_flag_and_preserves_executor_tuple(self):
        connection = self.grant_connection(self.named_connection(), "user-1")
        server = self.cache_entry(0, connection)
        headers = object()
        payload = {"reply": "Synthetic reply: Community workshop"}
        self.executor_result = (payload, headers)
        tools = self.invoke(
            [connection],
            [server],
            "server:synthetic_openapi",
            "user",
            include_response_headers=True,
        )
        result = await tools["echo"]["callable"](choice="Community workshop")
        self.assertIs(result, self.executor_result)
        self.assertEqual(self.server_calls[-1]["include_response_headers"], True)

    async def test_opt_in_does_not_forward_for_legacy_unnamed_server(self):
        connection = {
            "type": "openapi",
            "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        server = self.cache_entry(0, connection)
        tools = self.invoke(
            [connection],
            [server],
            "server:0",
            "user",
            include_response_headers=True,
        )
        self.assertEqual(set(tools), {"echo"})
        await tools["echo"]["callable"]()
        self.assertNotIn("include_response_headers", self.server_calls[-1])

    async def test_opt_in_still_requires_existing_named_read_gate(self):
        connection = self.named_connection()
        server = self.cache_entry(0, connection)
        self.assertEqual(
            self.invoke(
                [connection], [server], "server:synthetic_openapi", "user",
                include_response_headers=True,
            ),
            {},
        )
        self.assertEqual(self.server_calls, [])

    def test_pipe_injection_callsite_is_the_trusted_opt_in(self):
        tree = ast.parse(FUNCTIONS_SOURCE.read_text(encoding="utf-8"))
        assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == "extra_params"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "__tools__"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "get_tools"
            and any(
                keyword.arg == "include_response_headers"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.value.keywords
            )
        ]
        self.assertEqual(len(assignments), 1)

    async def test_only_exact_fresh_user_read_grant_allows_member_selection(self):
        base = self.named_connection()
        allowed = self.grant_connection(base, "user-1")
        reassigned = self.grant_connection(base, "user-2")
        server = self.cache_entry(0, base)

        self.assertTrue(self.namespace["named_tool_server_read_allowed"](allowed, self.user()))
        self.assertFalse(self.namespace["named_tool_server_read_allowed"](allowed, self.user("user-2")))
        self.assertTrue(self.namespace["named_tool_server_read_allowed"](base, self.user(role="admin")))
        self.assertFalse(self.namespace["named_tool_server_read_allowed"](base, self.user()))
        self.assertFalse(
            self.namespace["named_tool_server_read_allowed"](
                allowed, self.user(role="pending")
            )
        )
        self.assertFalse(
            self.namespace["named_tool_server_read_allowed"](
                allowed, self.user(role="unknown")
            )
        )
        empty_id_connection = self.grant_connection(base, "")
        self.assertFalse(
            self.namespace["named_tool_server_read_allowed"](
                empty_id_connection, self.user("")
            )
        )
        self.assertFalse(
            self.namespace["named_tool_server_read_allowed"](
                allowed, self.user("   ")
            )
        )

        unsupported_configs = [
            {},
            {"access_grants": None},
            {"access_grants": []},
            {"access_grants": "bad"},
            {"access_grants": [{"principal_type": "user", "principal_id": "user-1", "permission": "write"}]},
            {"access_grants": [{"principal_type": "group", "principal_id": "group-1", "permission": "read"}]},
            {"access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}]},
            {"access_grants": [{"principal_type": "user", "principal_id": "user-1", "permission": "read", "extra": True}]},
            {"access_grants": [
                {"principal_type": "user", "principal_id": "user-1", "permission": "read"},
                {"principal_type": "user", "principal_id": "user-2", "permission": "read"},
            ]},
        ]
        for config in unsupported_configs:
            with self.subTest(config=config):
                self.assertFalse(
                    self.namespace["named_tool_server_read_allowed"](
                        dict(base, config=config), self.user()
                    )
                )

        member_call = self.invoke(
            [allowed], [server], "server:synthetic_openapi", role="user", user_id="user-1"
        )
        self.assertEqual(set(member_call), {"echo"})
        self.assertEqual(
            self.invoke(
                [reassigned], [server], "server:synthetic_openapi", role="user", user_id="user-1"
            ),
            {},
        )
        self.assertEqual(
            self.invoke([base], [server], "server:synthetic_openapi", role="user", user_id="user-1"),
            {},
        )
        self.assertEqual(
            set(
                self.invoke(
                    [reassigned], [server], "server:synthetic_openapi", role="user", user_id="user-2"
                )
            ),
            {"echo"},
        )
        self.assertEqual(
            self.invoke([allowed], [server], "server:0", role="user", user_id="user-1"),
            {},
        )

    async def test_catalog_uses_fresh_grant_and_projects_only_named_objects(self):
        base = self.named_connection()
        allowed = self.grant_connection(base, "user-1")
        server = self.cache_entry(0, base)
        local = self.local_tool_record()

        member_rows = await self.catalog(allowed, server, self.user(), [local])
        self.assertEqual(len(member_rows), 2)
        local_row = next(row for row in member_rows if "version_id" not in row)
        named_row = next(row for row in member_rows if "version_id" in row)
        self.assertIn("content", local_row)
        self.assertIn("specs", local_row)
        self.assertEqual(local_row["access_grants"], [{"legacy": True}])
        self.assertEqual(
            set(named_row),
            {"access_grants", "created_at", "id", "meta", "name", "updated_at", "user", "user_id", "version_id"},
        )
        self.assertEqual(named_row["access_grants"], [])
        self.assertIsNone(named_row["version_id"])
        self.assertNotIn("user-1", repr(named_row))

        unrelated_rows = await self.catalog(allowed, server, self.user("user-2"), [local])
        self.assertEqual(unrelated_rows, [])
        denied_rows = await self.catalog(base, server, self.user(), [local])
        self.assertEqual(denied_rows, [member_rows[0]])

    async def test_legacy_numeric_catalog_keeps_legacy_metadata_shape(self):
        connection = {
            "type": "openapi",
            "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        server = {
            "idx": 0,
            "url": connection["url"],
            "specs": [],
            "openapi": {"info": {"title": "Legacy"}},
        }
        rows = await self.catalog(connection, server, self.user(role="admin"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "server:0")
        self.assertIn("access_control", rows[0])
        self.assertIn("content", rows[0])
        self.assertIn("specs", rows[0])
        self.assertNotIn("version_id", rows[0])

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
        padded_alias = "server:" + ("0" * 5000) + "0"
        self.assertEqual(self.invoke([connection], [server], padded_alias, "admin"), {})
        self.assertEqual(
            self.local_lookup_calls,
            ["server:synthetic_openapi"],
        )
        self.assertEqual(self.server_calls, [])
        huge_numeric = "server:" + ("9" * 5000)
        self.assertIsNone(
            self.namespace["resolve_tool_server_reference"](
                huge_numeric,
                [connection],
                [server],
                type("User", (), {"role": "admin", "id": "user-1"})(),
            )
        )

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

    async def test_local_tool_does_not_read_server_configuration(self):
        self.local_collision = True
        self.assertEqual(
            self.invoke(
                [], [], "local_tool", "user", refuse_config=True,
                include_response_headers=True,
            ),
            {},
        )
        self.assertEqual(self.local_lookup_calls, ["local_tool"])

    async def test_distinct_sparse_config_indices_keep_the_original_connection_index(self):
        legacy = {"type": "openapi", "url": "https://example.invalid/old.json", "config": {"enable": True}}
        named = self.named_connection("named")
        servers = [self.cache_entry(1, named)]
        catalog, _ = self.namespace["named_tool_server_catalog_ids"]([legacy, named], servers)
        self.assertEqual(catalog, {1: "server:named"})
        self.assertIs(
            self.namespace["resolve_tool_server_reference"](
                "server:named",
                [legacy, named],
                servers,
                type("User", (), {"role": "admin", "id": "user-1"})(),
            )[0],
            named,
        )

    async def test_catalog_refresh_replaces_eligible_named_schema_at_original_index(self):
        legacy = {
            "type": "openapi", "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        named = self.grant_connection(
            self.named_connection(
                "fresh", auth_type="none", key="", headers=None,
                forward_cookies=False,
            ),
            "user-1",
        )
        old_legacy = {
            "idx": 0, "url": legacy["url"], "specs": [],
            "openapi": {"info": {"title": "Legacy old"}},
        }
        old_named = self.cache_entry(1, named, specs=[{"name": "old_echo"}])
        route = self.load_catalog_route([])
        fetched = []

        async def fetch(selected):
            fetched.append(selected)
            return [{
                "idx": 0,
                "url": named["url"],
                "connection_binding": self.namespace["_tool_server_connection_binding"](named),
                "specs": [{"name": "echo_next"}],
                "openapi": {"info": {"title": "Fresh schema"}},
            }]

        self.catalog_fetcher = fetch
        config = type("Config", (), {"TOOL_SERVER_CONNECTIONS": [legacy, named]})()
        state = type("State", (), {"config": config, "TOOL_SERVERS": [old_legacy, old_named]})()
        request = type("RequestValue", (), {"app": type("App", (), {"state": state})()})()
        rows = await route(request, self.user(role="admin"))
        self.assertEqual(fetched, [[named]])
        self.assertEqual([entry["idx"] for entry in state.TOOL_SERVERS], [0, 1])
        self.assertEqual(state.TOOL_SERVERS[0], old_legacy)
        self.assertEqual(state.TOOL_SERVERS[1]["specs"], [{"name": "echo_next"}])
        self.assertEqual(
            [(row["id"], row["name"]) for row in rows],
            [("server:0", "Legacy old"), ("server:fresh", "Fresh schema")],
        )

    async def test_catalog_refresh_failure_removes_stale_named_entry_only(self):
        legacy = {
            "type": "openapi", "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        named = self.named_connection(
            "fresh", auth_type="none", key="", headers={},
        )
        old_legacy = {
            "idx": 0, "url": legacy["url"], "specs": [],
            "openapi": {"info": {"title": "Legacy"}},
        }
        old_named = self.cache_entry(1, named)
        route = self.load_catalog_route([])
        fetched = []

        async def fetch(selected):
            fetched.append(selected)
            return []

        self.catalog_fetcher = fetch
        config = type("Config", (), {"TOOL_SERVER_CONNECTIONS": [legacy, named]})()
        state = type("State", (), {"config": config, "TOOL_SERVERS": [old_legacy, old_named]})()
        request = type("RequestValue", (), {"app": type("App", (), {"state": state})()})()
        rows = await route(request, self.user(role="admin"))
        self.assertEqual(fetched, [[named]])
        self.assertEqual(state.TOOL_SERVERS, [old_legacy])
        self.assertEqual([(row["id"], row["name"]) for row in rows], [("server:0", "Legacy")])

    async def test_refresh_skips_ineligible_named_connections_and_no_eligible_is_unchanged(self):
        valid = self.named_connection("same", auth_type="none", key="")
        disabled = dict(valid, config={"enable": False})
        credentialed = dict(valid, auth_type="bearer", key="synthetic-secret-marker")
        credential_url = dict(valid, url="https://user:secret@example.invalid/openapi.json")
        forwarded_headers = dict(valid, headers={"X-Synthetic": "value"})
        forwarded_cookies = dict(valid, forward_cookies=True)
        malformed = self.named_connection("bad/id", auth_type="none", key="")
        malformed_duplicate = dict(valid, type="custom")
        duplicate = self.named_connection("same", auth_type="none", key="")
        cases = [
            [disabled],
            [credentialed],
            [credential_url],
            [forwarded_headers],
            [forwarded_cookies],
            [malformed],
            [valid, duplicate],
            [valid, malformed_duplicate],
        ]

        async def forbidden_fetch(_connections):
            self.fail("ineligible configuration unexpectedly triggered a refresh")

        route = self.load_catalog_route([])
        self.catalog_fetcher = forbidden_fetch
        for connections in cases:
            with self.subTest(connections=len(connections)):
                cached = [self.cache_entry(index, item) for index, item in enumerate(connections)]
                result = await self.catalog_refresh(connections, cached)
                self.assertIs(result, cached)

        local_only = [{"type": "openapi", "url": "https://example.invalid/local.json", "config": {"enable": True}}]
        cached = [self.cache_entry(0, local_only[0])]
        self.assertIs(await self.catalog_refresh(local_only, cached), cached)

    async def test_refresh_preserves_malformed_cached_indices_for_existing_route_guard(self):
        legacy = {
            "type": "openapi", "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
        }
        named = self.named_connection("fresh", auth_type="none", key="")
        old_legacy = {
            "idx": 0, "url": legacy["url"], "specs": [],
            "openapi": {"info": {"title": "Legacy"}},
        }
        old_named = self.cache_entry(1, named)
        malformed = [
            {"idx": [1], "sentinel": "list"},
            {"idx": {"value": 1}, "sentinel": "dict"},
            {"idx": None, "sentinel": "none"},
            {"idx": "1", "sentinel": "string"},
            {"idx": True, "sentinel": "bool"},
        ]
        route = self.load_catalog_route([])

        async def fetch(_selected):
            return [{
                "idx": 0,
                "url": named["url"],
                "connection_binding": self.namespace["_tool_server_connection_binding"](named),
                "specs": [{"name": "fresh_echo"}],
                "openapi": {"info": {"title": "Fresh"}},
            }]

        self.catalog_fetcher = fetch
        state = type("State", (), {
            "config": type("Config", (), {"TOOL_SERVER_CONNECTIONS": [legacy, named]})(),
            "TOOL_SERVERS": [*malformed, old_legacy, old_named],
        })()
        request = type("RequestValue", (), {"app": type("App", (), {"state": state})()})()
        rows = await route(request, self.user(role="admin"))
        self.assertEqual(state.TOOL_SERVERS[:len(malformed)], malformed)
        self.assertEqual(state.TOOL_SERVERS[len(malformed)], old_legacy)
        self.assertEqual(state.TOOL_SERVERS[-1]["idx"], 1)
        self.assertEqual([(row["id"], row["name"]) for row in rows], [
            ("server:0", "Legacy"), ("server:fresh", "Fresh")
        ])

    async def test_boolean_zero_from_loader_is_refused_without_stale_fallback(self):
        named = self.named_connection("fresh", auth_type="none", key="")
        stale = self.cache_entry(0, named, specs=[{"name": "stale_echo"}])
        route = self.load_catalog_route([])

        async def fetch(_selected):
            return [{
                "idx": False,
                "url": named["url"],
                "connection_binding": self.namespace["_tool_server_connection_binding"](named),
                "specs": [{"name": "wrong_echo"}],
                "openapi": {"info": {"title": "Wrong"}},
            }]

        self.catalog_fetcher = fetch
        state = type("State", (), {
            "config": type("Config", (), {"TOOL_SERVER_CONNECTIONS": [named]})(),
            "TOOL_SERVERS": [stale],
        })()
        request = type("RequestValue", (), {"app": type("App", (), {"state": state})()})()
        rows = await route(request, self.user(role="admin"))
        self.assertEqual(state.TOOL_SERVERS, [])
        self.assertEqual(rows, [])

    async def test_named_catalog_uses_configured_metadata_and_falls_back_for_invalid(self):
        connection = self.grant_connection(self.named_connection(), "user-1")
        connection["info"].update({
            "name": "Community workshop tools",
            "description": "Bounded synthetic tools — workshop",
        })
        server = self.cache_entry(0, connection)
        server["openapi"]["info"] = {
            "title": "Schema title",
            "description": "Schema description",
        }
        admin_rows = await self.catalog(connection, server, self.user(role="admin"))
        member_rows = await self.catalog(connection, server, self.user())
        for row in (admin_rows[0], member_rows[0]):
            self.assertEqual(row["name"], "Community workshop tools")
            self.assertEqual(row["meta"]["description"], "Bounded synthetic tools — workshop")

        invalid = self.grant_connection(self.named_connection(), "user-1")
        invalid["info"]["name"] = "  "
        invalid["info"]["description"] = 17
        fallback_server = self.cache_entry(0, invalid)
        fallback_server["openapi"]["info"] = {
            "title": "Fallback schema title",
            "description": "Fallback schema description",
        }
        fallback = await self.catalog(invalid, fallback_server, self.user(role="admin"))
        self.assertEqual(fallback[0]["name"], "Fallback schema title")
        self.assertEqual(fallback[0]["meta"]["description"], "Fallback schema description")
        invalid["info"]["name"] = "bad\nname"
        invalid["info"]["description"] = ""
        control_server = self.cache_entry(0, invalid)
        control_server["openapi"]["info"] = {
            "title": "Fallback schema title",
            "description": "Fallback schema description",
        }
        control_fallback = await self.catalog(
            invalid, control_server, self.user(role="admin")
        )
        self.assertEqual(control_fallback[0]["name"], "Fallback schema title")
        self.assertEqual(control_fallback[0]["meta"]["description"], "")

    async def test_named_metadata_projection_does_not_change_legacy_or_local_rows(self):
        legacy = {
            "type": "openapi",
            "url": "https://example.invalid/legacy.json",
            "config": {"enable": True},
            "info": {"name": "Ignored legacy name", "description": "Ignored legacy description"},
        }
        server = {
            "idx": 0,
            "url": legacy["url"],
            "specs": [],
            "openapi": {"info": {"title": "Legacy schema title", "description": "Legacy schema description"}},
        }
        local = self.local_tool_record("local_tool")
        rows = await self.catalog(legacy, server, self.user(role="admin"), [local])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["server:0"]["name"], "Legacy schema title")
        self.assertEqual(by_id["server:0"]["meta"]["description"], "Legacy schema description")
        self.assertEqual(by_id["local_tool"]["name"], "Local colliding tool")


class ToolServerExecutorResponseTests(unittest.IsolatedAsyncioTestCase):
    def executor(self, response):
        class Response:
            status = response.status
            headers = response.headers

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def json(self):
                if isinstance(response.json_result, Exception):
                    raise response.json_result
                return response.json_result

            async def text(self):
                return response.text_result

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def post(self, *_args, **_kwargs):
                return Response()

        namespace = {
            "Dict": dict,
            "Any": object,
            "aiohttp": SimpleNamespace(ClientSession=Session),
            "print": lambda *_args, **_kwargs: None,
        }
        _load_functions(UTILS_SOURCE, {"execute_tool_server"}, namespace)
        return namespace["execute_tool_server"]

    def server_data(self):
        return {
            "openapi": {
                "paths": {
                    "/echo": {
                        "post": {
                            "operationId": "synthetic_echo",
                            "requestBody": {"content": {"application/json": {}}},
                        }
                    }
                }
            }
        }

    async def test_default_returns_payload_and_opt_in_returns_actual_header_object(self):
        headers = object()
        payload = {"reply": "Synthetic reply: Festival poster"}
        fake = SimpleNamespace(
            status=200, headers=headers, json_result=payload, text_result=""
        )
        execute = self.executor(fake)
        args = {
            "token": None,
            "url": "http://127.0.0.1:18081",
            "name": "synthetic_echo",
            "params": {"choice": "Festival poster"},
            "server_data": self.server_data(),
        }
        self.assertIs(await execute(**args), payload)
        result = await execute(**args, include_response_headers=True)
        self.assertIs(result[0], payload)
        self.assertIs(result[1], headers)

    async def test_http_and_json_errors_remain_raw_error_dicts_when_opted_in(self):
        http_failure = SimpleNamespace(
            status=503,
            headers=object(),
            json_result={"should": "not be returned"},
            text_result="Synthetic unavailable",
        )
        result = await self.executor(http_failure)(
            None,
            "http://127.0.0.1:18081",
            "synthetic_echo",
            {"choice": "Community workshop"},
            self.server_data(),
            include_response_headers=True,
        )
        self.assertEqual(result, {"error": "HTTP error 503: Synthetic unavailable"})

        decode_failure = SimpleNamespace(
            status=200,
            headers=object(),
            json_result=ValueError("Synthetic invalid JSON"),
            text_result="",
        )
        result = await self.executor(decode_failure)(
            None,
            "http://127.0.0.1:18081",
            "synthetic_echo",
            {"choice": "Community workshop"},
            self.server_data(),
            include_response_headers=True,
        )
        self.assertEqual(result, {"error": "Synthetic invalid JSON"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
