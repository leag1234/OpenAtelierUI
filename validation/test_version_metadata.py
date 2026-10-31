#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Extracted direct-GET route tests for imported current-version pointers."""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace


class RouteHTTPException(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Response(dict):
    def __init__(self, initial, events):
        super().__init__(initial)
        self.events = events

    def pop(self, key, *args):
        if key == "content":
            self.events.append("redact_content")
        return super().pop(key, *args)

    def __setitem__(self, key, value):
        if key == "params":
            self.events.append("redact_params")
        super().__setitem__(key, value)


def call_direct_get(kind, *, role="user", user_id="owner", owner="owner", allowed=False, metadata="version-uuid"):
    filename = "tools.py" if kind == "tool" else "models.py"
    function_name = "get_tools_by_id" if kind == "tool" else "get_model_by_id"
    field_name = "content" if kind == "tool" else "params"
    route_path = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "routers" / filename
    tree = ast.parse(route_path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name)
    function.decorator_list = []
    from test_imported_history import HistoryNotFound, HistoryUnavailable

    events = []
    resource = SimpleNamespace(
        id=f"synthetic-{kind}", user_id=owner, access_control=None, created_at=1,
        model_dump=lambda: Response({field_name: "private source or settings", "user_id": owner}, events),
    )

    class Table:
        @staticmethod
        def get_tool_by_id(_id):
            events.append("resource_lookup")
            return resource

        @staticmethod
        def get_model_by_id(_id):
            events.append("resource_lookup")
            return resource

    def has_access(_user_id, permission, _acl):
        events.append("read_acl")
        return permission == "read" and allowed

    def access_fields(_resource, _user):
        events.append("access_projection")
        return {"write_access": False}

    def version_lookup(_data_dir, _database_url, lookup_kind, lookup_resource):
        events.append("version_lookup")
        assert lookup_kind == kind
        assert lookup_resource is resource
        if isinstance(metadata, BaseException):
            raise metadata
        return metadata

    namespace = {
        "router": SimpleNamespace(),
        "Depends": lambda dependency: None,
        "get_verified_user": lambda: None,
        "HTTPException": RouteHTTPException,
        "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
        "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="Access prohibited", NOT_FOUND="Not found"),
        "HistoryNotFound": HistoryNotFound,
        "HistoryUnavailable": HistoryUnavailable,
        "Tools": Table,
        "Models": Table,
        "has_access": has_access,
        "_tool_access_fields": access_fields,
        "_model_access_fields": access_fields,
        "get_imported_tool_current_version_id": version_lookup,
        "get_imported_model_current_version_id": version_lookup,
        "DATA_DIR": "/synthetic/data",
        "DATABASE_URL": "sqlite:////synthetic/data/webui.db",
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(route_path), "exec"), namespace)
    user = SimpleNamespace(id=user_id, role=role)
    route = namespace[function_name]
    try:
        result = asyncio.run(route(resource.id, user=user))
        return result, None, events
    except RouteHTTPException as error:
        return None, error, events


class VersionMetadataRouteTests(unittest.TestCase):
    def test_authorized_direct_gets_add_exact_pointer_after_read_redaction(self):
        for kind, redaction_event, redacted_value in (
            ("tool", "redact_content", None),
            ("model", "redact_params", {}),
        ):
            result, error, events = call_direct_get(kind, user_id="reader", allowed=True)
            self.assertIsNone(error)
            self.assertEqual(result["version_id"], "version-uuid")
            self.assertIn("version_lookup", events)
            self.assertLess(events.index(redaction_event), events.index("version_lookup"))
            if kind == "tool":
                self.assertNotIn("content", result)
            else:
                self.assertEqual(result["params"], redacted_value)

    def test_private_denial_precedes_metadata_lookup(self):
        for kind in ("tool", "model"):
            result, error, events = call_direct_get(kind, user_id="unrelated", allowed=False)
            self.assertIsNone(result)
            self.assertEqual(error.status_code, 401)
            self.assertNotIn("version_lookup", events)
            self.assertNotIn("redact_content", events)
            self.assertNotIn("redact_params", events)

    def test_missing_disabled_and_stale_history_fall_back_to_explicit_null(self):
        from test_imported_history import HistoryNotFound, HistoryUnavailable

        for kind in ("tool", "model"):
            for failure in (HistoryNotFound(), HistoryUnavailable("disabled"), HistoryUnavailable("stale")):
                result, error, events = call_direct_get(kind, metadata=failure)
                self.assertIsNone(error)
                self.assertIn("version_id", result)
                self.assertIsNone(result["version_id"])
                self.assertIn("version_lookup", events)

    def test_admin_direct_get_uses_same_pointer_without_changing_private_redaction(self):
        for kind in ("tool", "model"):
            result, error, events = call_direct_get(kind, role="admin", user_id="admin")
            self.assertIsNone(error)
            self.assertEqual(result["version_id"], "version-uuid")
            self.assertNotIn("read_acl", events)
            if kind == "tool":
                self.assertEqual(result["content"], "private source or settings")
            else:
                self.assertEqual(result["params"], "private source or settings")


if __name__ == "__main__":
    unittest.main(verbosity=2)
