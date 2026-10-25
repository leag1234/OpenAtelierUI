#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Exercise the checked-in private configuration route bodies with isolated stubs."""

from __future__ import annotations

import ast
import copy
import pathlib
import unittest
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]


class HTTPError(Exception):
    def __init__(self, status_code, detail=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def compile_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    missing = set(names) - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"missing route functions in {path}: {missing}")
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


class User:
    def __init__(self, identity, role="user"):
        self.id = identity
        self.role = role


class ToolRow:
    def __init__(self):
        self.id = "tool-shared"
        self.user_id = "owner"
        self.access_control = {"read": {"user_ids": ["reader"]}}


class ToolStore:
    row = ToolRow()
    config_reads = 0

    @classmethod
    def get_tool_by_id(cls, identity):
        return cls.row if identity == cls.row.id else None

    @classmethod
    def get_tool_valves_by_id(cls, identity):
        cls.config_reads += 1
        return {"token": "owner-only-synthetic-config", "timeout": 7}


class ModelRow:
    def __init__(self, access_control):
        self.id = "model-synthetic"
        self.user_id = "owner"
        self.access_control = access_control
        self.created_at = 123
        self.params = {"temperature": 0.25, "seed": 17}
        self.name = "Synthetic model"
        self.base_model_id = "portable_pipe"
        self.meta = {"description": "synthetic"}
        self.is_active = True
        self.updated_at = 123

    def model_dump(self):
        return copy.deepcopy({
            "id": self.id,
            "user_id": self.user_id,
            "base_model_id": self.base_model_id,
            "name": self.name,
            "params": self.params,
            "meta": self.meta,
            "access_control": self.access_control,
            "is_active": self.is_active,
            "updated_at": self.updated_at,
            "created_at": self.created_at,
        })


class ModelStore:
    row = None

    @classmethod
    def get_model_by_id(cls, identity):
        return cls.row if identity == "model-synthetic" else None


def has_access(identity, permission, access_control):
    if access_control is None:
        return True
    return bool(access_control and identity in access_control.get(permission, {}).get("user_ids", []))


def access_grants_for_acl(access_control, resource_type, resource_id, created_at):
    if access_control is None:
        return [{"resource_type": resource_type, "resource_id": resource_id, "principal_id": "*", "permission": "read", "created_at": created_at}]
    return []


def _route_namespace():
    return {
        "Depends": lambda value: value,
        "get_verified_user": lambda: None,
        "HTTPException": HTTPError,
        "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_400_BAD_REQUEST=400),
        "ERROR_MESSAGES": SimpleNamespace(UNAUTHORIZED="unauthorized", NOT_FOUND="not found", DEFAULT=lambda value="error": value),
        "Tools": ToolStore,
        "Models": ModelStore,
        "has_access": has_access,
        "access_grants_for_acl": access_grants_for_acl,
    }


tool_route = compile_functions(
    ROOT / "backend/open_webui/routers/tools.py", {"get_tools_valves_by_id"}, _route_namespace()
)["get_tools_valves_by_id"]
model_routes = compile_functions(
    ROOT / "backend/open_webui/routers/models.py", {"_model_access_fields", "get_model_by_id"}, _route_namespace()
)
model_route = model_routes["get_model_by_id"]


class PrivateConfigurationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_valves_require_owner_or_admin_before_configuration_read(self):
        for identity in ("reader", "unrelated"):
            with self.subTest(identity=identity):
                ToolStore.config_reads = 0
                with self.assertRaises(HTTPError) as caught:
                    await tool_route("tool-shared", User(identity))
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(ToolStore.config_reads, 0)

    async def test_valves_owner_and_admin_keep_exact_values(self):
        expected = {"token": "owner-only-synthetic-config", "timeout": 7}
        for user in (User("owner"), User("administrator", "admin")):
            with self.subTest(role=user.role):
                ToolStore.config_reads = 0
                self.assertEqual(await tool_route("tool-shared", user), expected)
                self.assertEqual(ToolStore.config_reads, 1)

    async def test_model_reader_gets_response_only_empty_params(self):
        ModelStore.row = ModelRow({"read": {"user_ids": ["reader"]}})
        persisted_before = copy.deepcopy(ModelStore.row.params)
        response = await model_route("model-synthetic", User("reader"))
        self.assertEqual(response["params"], {})
        self.assertEqual(response["name"], "Synthetic model")
        self.assertEqual(response["access_control"], ModelStore.row.access_control)
        self.assertEqual(ModelStore.row.params, persisted_before)

    async def test_public_reader_is_redacted_but_owner_and_admin_keep_values(self):
        ModelStore.row = ModelRow(None)
        expected_params = {"temperature": 0.25, "seed": 17}
        reader = await model_route("model-synthetic", User("reader"))
        self.assertEqual(reader["params"], {})
        self.assertEqual(ModelStore.row.params, expected_params)
        for user in (User("owner"), User("administrator", "admin")):
            with self.subTest(role=user.role):
                response = await model_route("model-synthetic", user)
                self.assertEqual(response["params"], expected_params)
                self.assertEqual(ModelStore.row.params, expected_params)

    async def test_unrelated_private_model_remains_denied_without_mutation(self):
        ModelStore.row = ModelRow({})
        before = copy.deepcopy(ModelStore.row.params)
        with self.assertRaises(HTTPError) as caught:
            await model_route("model-synthetic", User("unrelated"))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(ModelStore.row.params, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
