#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Extracted-route tests for the owner Tool workspace-capability gate."""

from __future__ import annotations

import ast
import asyncio
import copy
import unittest
from pathlib import Path
from types import SimpleNamespace


class RouteHTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def call_update_route(*, role="user", user_id="owner", capability=False, tool_owner="owner", access_control=None):
    source = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "routers" / "tools.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_tools_by_id")
    function.decorator_list = []
    events = []
    permissions = {"workspace": {"tools": capability, "models": True}, "other": True}
    permissions_before = copy.deepcopy(permissions)

    tool = SimpleNamespace(
        id="synthetic-tool",
        user_id=tool_owner,
        access_control=access_control,
        model_dump=lambda: {"id": "synthetic-tool", "user_id": tool_owner, "content": "updated"},
    )
    updated_tool = SimpleNamespace(
        id="synthetic-tool",
        user_id=tool_owner,
        access_control=access_control,
        model_dump=lambda: {"id": "synthetic-tool", "user_id": tool_owner, "content": "updated"},
    )

    class ToolTable:
        @staticmethod
        def get_tool_by_id(_id):
            events.append("lookup")
            return tool

        @staticmethod
        def update_tool_by_id(_id, _updated):
            events.append("store")
            return updated_tool

    class TrackingCache(dict):
        def __setitem__(self, key, value):
            events.append("cache_write")
            super().__setitem__(key, value)

    def event(name, result=None):
        def inner(*args, **kwargs):
            events.append(name)
            return result(*args, **kwargs) if callable(result) else result
        return inner

    def has_permission(_user_id, permission, configured):
        events.append("permission_check")
        assert permission == "workspace.tools"
        return bool(configured.get("workspace", {}).get("tools", False))

    form_data = SimpleNamespace(
        id="synthetic-tool",
        content="synthetic candidate source",
        meta=SimpleNamespace(manifest=None),
        model_dump=lambda **kwargs: {"name": "Updated synthetic tool", "content": "synthetic candidate source", "meta": {}},
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                TOOLS=TrackingCache(),
                config=SimpleNamespace(USER_PERMISSIONS=permissions),
            )
        )
    )
    namespace = {
        "Request": object,
        "ToolForm": object,
        "Depends": lambda _dependency: None,
        "get_verified_user": lambda: None,
        "HTTPException": RouteHTTPException,
        "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_400_BAD_REQUEST=400),
        "ERROR_MESSAGES": SimpleNamespace(
            UNAUTHORIZED="401 Unauthorized",
            NOT_FOUND="Not found",
            DEFAULT=lambda message: message,
        ),
        "Tools": ToolTable,
        "has_access": event("write_acl_check", False),
        "has_permission": has_permission,
        "replace_imports": event("replace_imports", lambda content: content),
        "load_tool_module_by_id": event("load_module", lambda *args, **kwargs: (SimpleNamespace(), {})),
        "get_tool_specs": event("get_specs", []),
        "preserve_access_control_for_update": event("preserve_acl", lambda updated, _acl: updated),
        "_tool_access_fields": lambda _tool, _user: {"access_grants": [], "write_access": True},
        "log": SimpleNamespace(debug=lambda _value: events.append("debug")),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(source), "exec"), namespace)
    route = namespace["update_tools_by_id"]
    user = SimpleNamespace(id=user_id, role=role)
    result = None
    error = None
    try:
        result = asyncio.run(route(request, "synthetic-tool", form_data, user))
    except RouteHTTPException as exc:
        error = exc
    return result, error, events, permissions, permissions_before


class ToolCapabilityGateTests(unittest.TestCase):
    def test_disabled_owner_is_denied_before_plugin_cache_or_store_effects(self):
        result, error, events, permissions, before = call_update_route(capability=False)
        self.assertIsNone(result)
        self.assertEqual((error.status_code, error.detail), (401, "401 Unauthorized"))
        self.assertEqual(events, ["lookup", "permission_check"])
        self.assertEqual(permissions, before)

    def test_enabled_owner_continues_through_existing_update_path(self):
        result, error, events, permissions, before = call_update_route(capability=True)
        self.assertIsNone(error)
        self.assertEqual(result["content"], "updated")
        self.assertEqual(events, ["lookup", "permission_check", "replace_imports", "load_module", "cache_write", "get_specs", "preserve_acl", "debug", "store"])
        self.assertEqual(permissions, before)

    def test_revocation_takes_effect_on_the_next_request(self):
        first = call_update_route(capability=True)
        second = call_update_route(capability=False)
        self.assertIsNone(first[1])
        self.assertEqual((second[1].status_code, second[1].detail), (401, "401 Unauthorized"))
        self.assertIn("store", first[2])
        self.assertNotIn("store", second[2])

    def test_administrator_bypasses_disabled_capability(self):
        result, error, events, permissions, before = call_update_route(role="admin", user_id="admin", capability=False)
        self.assertIsNone(error)
        self.assertEqual(result["content"], "updated")
        self.assertNotIn("permission_check", events)
        self.assertIn("store", events)
        self.assertEqual(permissions, before)

    def test_nonowner_remains_denied_even_when_capability_is_enabled(self):
        result, error, events, permissions, before = call_update_route(capability=True, user_id="unrelated", tool_owner="owner")
        self.assertIsNone(result)
        self.assertEqual((error.status_code, error.detail), (401, "401 Unauthorized"))
        self.assertEqual(events, ["lookup", "write_acl_check"])
        self.assertEqual(permissions, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
