# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused security and route checks for the MCP application bridge."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import hmac
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ROUTER_SOURCE = ROOT / "backend/open_webui/routers/tools.py"
MCP_SOURCE = ROOT / "backend/open_webui/utils/mcp_apps.py"


def _load_pure_helpers():
    names = {
        "_mcp_connection_id",
        "resolve_mcp_connection",
        "create_mcp_app_binding",
        "verify_mcp_app_binding",
        "validate_mcp_app_request",
        "shape_mcp_app_result",
    }
    tree = ast.parse(MCP_SOURCE.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_MCP_ID_CHARS"
            for target in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            nodes.append(node)
    namespace = {
        "Any": Any,
        "Optional": Optional,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "urlsplit": urlsplit,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(MCP_SOURCE), "exec"), namespace)
    return namespace


_HELPERS = _load_pure_helpers()
create_mcp_app_binding = _HELPERS["create_mcp_app_binding"]
resolve_mcp_connection = _HELPERS["resolve_mcp_connection"]
shape_mcp_app_result = _HELPERS["shape_mcp_app_result"]
validate_mcp_app_request = _HELPERS["validate_mcp_app_request"]
verify_mcp_app_binding = _HELPERS["verify_mcp_app_binding"]


class RouteHTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _connection(identifier="synthetic", grants=None):
    return {
        "type": "mcp",
        "url": "http://mcp.invalid/mcp",
        "auth_type": "none",
        "info": {"id": identifier, "name": "Synthetic MCP"},
        "config": {"enable": True, "access_grants": grants or []},
    }


class MCPAppPureTests(unittest.TestCase):
    def test_binding_is_user_server_and_tool_bound(self):
        binding = create_mcp_app_binding("secret", "owner", "synthetic", "echo")
        self.assertRegex(binding, r"^[a-f0-9]{64}$")
        self.assertTrue(
            verify_mcp_app_binding(
                binding, "secret", "owner", "synthetic", "echo"
            )
        )
        for changed in (
            ("other", "synthetic", "echo"),
            ("owner", "other", "echo"),
            ("owner", "synthetic", "other"),
        ):
            self.assertFalse(
                verify_mcp_app_binding(binding, "secret", *changed)
            )
        self.assertFalse(
            verify_mcp_app_binding(
                binding, "", "owner", "synthetic", "echo"
            )
        )

    def test_resolver_requires_unique_enabled_readable_connection(self):
        admin = SimpleNamespace(id="admin", role="admin")
        user = SimpleNamespace(id="owner", role="user")
        grant = {
            "principal_type": "user",
            "principal_id": "owner",
            "permission": "read",
        }
        connection = _connection(grants=[grant])
        self.assertEqual(
            resolve_mcp_connection("server:mcp:synthetic", [connection], admin),
            (connection, "synthetic"),
        )
        self.assertEqual(
            resolve_mcp_connection("server:mcp:synthetic", [connection], user),
            (connection, "synthetic"),
        )
        self.assertIsNone(
            resolve_mcp_connection(
                "server:mcp:synthetic",
                [connection],
                SimpleNamespace(id="other", role="user"),
            )
        )
        self.assertIsNone(
            resolve_mcp_connection(
                "server:mcp:synthetic", [connection, connection], admin
            )
        )

    def test_request_shapes_are_exact_and_fail_closed(self):
        self.assertEqual(
            validate_mcp_app_request(
                "echo",
                "tools/call",
                {"name": "echo", "arguments": {"label": "Community workshop"}},
            )["name"],
            "echo",
        )
        self.assertEqual(validate_mcp_app_request("echo", "resources/list", {}), {})
        with self.assertRaisesRegex(ValueError, "Invalid app request"):
            validate_mcp_app_request(
                "echo", "tools/call", {"name": "other", "arguments": {}}
            )
        with self.assertRaisesRegex(ValueError, "Invalid app request"):
            validate_mcp_app_request("echo", "ui/resource", {"uri": "ui://x"})

    def test_results_match_measured_complete_response_shape(self):
        self.assertEqual(
            shape_mcp_app_result(
                "tools/call", {"content": [], "isError": False}
            ),
            {"content": [], "isError": False, "resultType": "complete"},
        )
        self.assertEqual(
            shape_mcp_app_result("resources/list", {"resources": []}),
            {
                "resources": [],
                "resultType": "complete",
                "cacheScope": "private",
                "ttlMs": 0,
            },
        )


class MCPAppRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "mcp_app_rpc"
        )
        function.decorator_list = []
        self.calls = []
        self.resolved = (_connection(), "synthetic")
        self.binding_valid = True

        async def call(connection, method, params):
            self.calls.append((connection, method, params))
            return {"resources": []}

        namespace = {
            "Request": object,
            "MCPAppRPCForm": object,
            "Depends": lambda dependency: dependency,
            "get_verified_user": lambda: None,
            "resolve_mcp_connection": lambda *_args: self.resolved,
            "verify_mcp_app_binding": lambda *_args: self.binding_valid,
            "validate_mcp_app_request": validate_mcp_app_request,
            "call_mcp_method": call,
            "shape_mcp_app_result": shape_mcp_app_result,
            "HTTPException": RouteHTTPException,
            "WEBUI_SECRET_KEY": "secret",
        }
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                str(ROUTER_SOURCE),
                "exec",
            ),
            namespace,
        )
        self.route = namespace["mcp_app_rpc"]

    @staticmethod
    def request():
        return SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=SimpleNamespace(TOOL_SERVER_CONNECTIONS=[])
                )
            )
        )

    @staticmethod
    def form(method="resources/list", params=None):
        return SimpleNamespace(
            tool_name="echo",
            binding="0" * 64,
            method=method,
            params={} if params is None else params,
        )

    async def test_valid_call_is_proxied_and_shaped(self):
        result = await self.route(
            self.request(),
            "server:mcp:synthetic",
            self.form(),
            SimpleNamespace(id="owner", role="user"),
        )
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["cacheScope"], "private")
        self.assertEqual(len(self.calls), 1)

    async def test_missing_and_forged_bindings_have_no_network_effect(self):
        self.resolved = None
        with self.assertRaises(RouteHTTPException) as missing:
            await self.route(
                self.request(), "server:mcp:missing", self.form(), SimpleNamespace(id="owner")
            )
        self.assertEqual(missing.exception.status_code, 404)
        self.resolved = (_connection(), "synthetic")
        self.binding_valid = False
        with self.assertRaises(RouteHTTPException) as forged:
            await self.route(
                self.request(), "server:mcp:synthetic", self.form(), SimpleNamespace(id="owner")
            )
        self.assertEqual(forged.exception.status_code, 403)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
