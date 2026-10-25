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
    user_config_reads = 0
    user_valve_updates = []
    user_valve_values = {}

    @classmethod
    def get_tool_by_id(cls, identity):
        return cls.row if identity == cls.row.id else None

    @classmethod
    def get_tool_valves_by_id(cls, identity):
        cls.config_reads += 1
        return {"token": "owner-only-synthetic-config", "timeout": 7}

    @classmethod
    def get_user_valves_by_id_and_user_id(cls, identity, user_id):
        cls.user_config_reads += 1
        return {}

    @classmethod
    def update_user_valves_by_id_and_user_id(cls, identity, user_id, valves):
        cls.user_valve_updates.append((identity, user_id, copy.deepcopy(valves)))
        cls.user_valve_values[(identity, user_id)] = copy.deepcopy(valves)


class SyntheticUserValves:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return copy.deepcopy(self.values)


class SpecState:
    loader_calls = 0
    schema_calls = 0
    user_valves_enabled = False

    @classmethod
    def load(cls, identity):
        cls.loader_calls += 1
        module = SimpleNamespace(Valves=cls)
        if cls.user_valves_enabled:
            module.UserValves = SyntheticUserValves
        return module, None

    @classmethod
    def schema(cls):
        cls.schema_calls += 1
        return {"title": "Synthetic private valves schema"}


class ProbeCache(dict):
    def __init__(self):
        super().__init__()
        self.contains_calls = 0

    def __contains__(self, key):
        self.contains_calls += 1
        return super().__contains__(key)


class ListedTool:
    def __init__(self, identity, access_control):
        self.id = identity
        self.user_id = "owner"
        self.name = "Synthetic " + identity
        self.access_control = access_control
        self.meta = {"description": "synthetic metadata"}
        self.created_at = 123
        self.updated_at = 124
        self.specs = [{"name": "run"}]

    def model_dump(self, exclude=None):
        data = {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "content": "synthetic source",
            "access_control": self.access_control,
        }
        return {key: value for key, value in data.items() if not exclude or key not in exclude}


class ToolListStore:
    rows = [
        ListedTool("private", {}),
        ListedTool("targeted", {"read": {"user_ids": ["reader"]}}),
        ListedTool("public", None),
    ]
    requested_permissions = []

    @classmethod
    def get_tools(cls):
        return list(cls.rows)

    @classmethod
    def get_tools_by_user_id(cls, identity, permission):
        cls.requested_permissions.append(permission)
        return [row for row in cls.rows if row.user_id == identity or has_access(identity, permission, row.access_control)]


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
        "Request": object,
        "HTTPException": HTTPError,
        "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_400_BAD_REQUEST=400),
        "ERROR_MESSAGES": SimpleNamespace(
            UNAUTHORIZED="unauthorized",
            ACCESS_PROHIBITED="You do not have permission to access this resource. Please contact your administrator for assistance.",
            NOT_FOUND="not found",
            DEFAULT=lambda value="error": value,
        ),
        "Tools": ToolStore,
        "load_tool_module_by_id": SpecState.load,
        "Models": ModelStore,
        "has_access": has_access,
        "access_grants_for_acl": access_grants_for_acl,
    }


tool_routes = compile_functions(
    ROOT / "backend/open_webui/routers/tools.py",
    {"_tool_access_fields", "get_tools_by_id", "get_tools_valves_by_id", "get_tools_valves_spec_by_id", "get_tools_user_valves_by_id", "get_tools_user_valves_spec_by_id", "update_tools_user_valves_by_id"},
    _route_namespace(),
)
tool_route = tool_routes["get_tools_valves_by_id"]
tool_by_id_route = tool_routes["get_tools_by_id"]
tool_spec_route = tool_routes["get_tools_valves_spec_by_id"]
user_valves_route = tool_routes["get_tools_user_valves_by_id"]
user_valves_spec_route = tool_routes["get_tools_user_valves_spec_by_id"]
update_user_valves_route = tool_routes["update_tools_user_valves_by_id"]
list_namespace = _route_namespace()
list_namespace["Tools"] = ToolListStore
list_namespace["_tool_access_fields"] = tool_routes["_tool_access_fields"]
tool_list_route = compile_functions(
    ROOT / "backend/open_webui/routers/tools.py", {"get_tool_list"}, list_namespace
)["get_tool_list"]
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
                self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
                self.assertEqual(ToolStore.config_reads, 0)

    async def test_valves_owner_and_admin_keep_exact_values(self):
        expected = {"token": "owner-only-synthetic-config", "timeout": 7}
        for user in (User("owner"), User("administrator", "admin")):
            with self.subTest(role=user.role):
                ToolStore.config_reads = 0
                self.assertEqual(await tool_route("tool-shared", user), expected)
                self.assertEqual(ToolStore.config_reads, 1)

    async def test_valves_spec_denies_before_cache_probe_or_module_load(self):
        for identity, access_control in (
            ("reader", {"read": {"user_ids": ["reader"]}}),
            ("public-reader", None),
            ("unrelated", {"read": {"user_ids": ["reader"]}}),
        ):
            with self.subTest(identity=identity, access_control=access_control):
                ToolStore.row.access_control = access_control
                SpecState.loader_calls = 0
                SpecState.schema_calls = 0
                cache = ProbeCache()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
                with self.assertRaises(HTTPError) as caught:
                    await tool_spec_route(request, "tool-shared", User(identity))
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
                self.assertEqual(cache.contains_calls, 0)
                self.assertEqual(SpecState.loader_calls, 0)
                self.assertEqual(SpecState.schema_calls, 0)
                self.assertEqual(cache, {})
        ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}

    async def test_valves_spec_owner_and_admin_receive_schema(self):
        for user in (User("owner"), User("administrator", "admin")):
            with self.subTest(role=user.role):
                SpecState.loader_calls = 0
                SpecState.schema_calls = 0
                cache = ProbeCache()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
                self.assertEqual(await tool_spec_route(request, "tool-shared", user), {"title": "Synthetic private valves schema"})
                self.assertEqual(SpecState.loader_calls, 1)
                self.assertEqual(SpecState.schema_calls, 1)
                self.assertIn("tool-shared", cache)

    async def test_user_valve_values_require_resource_read_before_storage(self):
        cases = (
            ("owner", "user", {}, True),
            ("administrator", "admin", {}, True),
            ("reader", "user", {"read": {"user_ids": ["reader"]}}, True),
            ("public-reader", "user", None, True),
            ("unrelated", "user", {"read": {"user_ids": ["reader"]}}, False),
            ("private-reader", "user", {}, False),
        )
        for identity, role, access_control, allowed in cases:
            with self.subTest(identity=identity, access_control=access_control):
                ToolStore.row.access_control = access_control
                ToolStore.user_config_reads = 0
                if allowed:
                    self.assertEqual(await user_valves_route("tool-shared", User(identity, role)), {})
                    self.assertEqual(ToolStore.user_config_reads, 1)
                else:
                    with self.assertRaises(HTTPError) as caught:
                        await user_valves_route("tool-shared", User(identity, role))
                    self.assertEqual(caught.exception.status_code, 401)
                    self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
                    self.assertEqual(ToolStore.user_config_reads, 0)
        ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}

    async def test_user_valve_spec_access_and_denial_before_cache_or_module(self):
        cases = (
            ("owner", "user", {}, True),
            ("administrator", "admin", {}, True),
            ("reader", "user", {"read": {"user_ids": ["reader"]}}, True),
            ("public-reader", "user", None, True),
            ("unrelated", "user", {"read": {"user_ids": ["reader"]}}, False),
            ("private-reader", "user", {}, False),
        )
        for identity, role, access_control, allowed in cases:
            with self.subTest(identity=identity, access_control=access_control):
                ToolStore.row.access_control = access_control
                SpecState.loader_calls = 0
                SpecState.schema_calls = 0
                cache = ProbeCache()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
                user = User(identity, role)
                if allowed:
                    self.assertIsNone(await user_valves_spec_route(request, "tool-shared", user))
                    self.assertEqual(SpecState.loader_calls, 1)
                    self.assertIs(cache["tool-shared"].Valves, SpecState)
                else:
                    with self.assertRaises(HTTPError) as caught:
                        await user_valves_spec_route(request, "tool-shared", user)
                    self.assertEqual(caught.exception.status_code, 401)
                    self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
                    self.assertEqual(cache.contains_calls, 0)
                    self.assertEqual(SpecState.loader_calls, 0)
                    self.assertEqual(SpecState.schema_calls, 0)
                    self.assertEqual(cache, {})
        ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}

    async def test_user_valve_update_denies_before_cache_module_or_write(self):
        ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}
        for identity, access_control in (
            ("private-reader", {"read": {"user_ids": ["reader"]}}),
            ("unrelated", {"read": {"user_ids": ["reader"]}}),
        ):
            with self.subTest(identity=identity):
                ToolStore.row.access_control = access_control
                ToolStore.user_valve_updates = []
                ToolStore.user_valve_values = {}
                SpecState.loader_calls = 0
                cache = ProbeCache()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
                with self.assertRaises(HTTPError) as caught:
                    await update_user_valves_route(request, "tool-shared", {}, User(identity))
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
                self.assertEqual(cache.contains_calls, 0)
                self.assertEqual(SpecState.loader_calls, 0)
                self.assertEqual(cache, {})
                self.assertEqual(ToolStore.user_valve_updates, [])
                self.assertEqual(ToolStore.user_valve_values, {})

    async def test_user_valve_update_keeps_allowed_missing_class_and_scoped_class_behavior(self):
        cases = (
            (User("owner"), {}),
            (User("administrator", "admin"), {}),
            (User("reader"), {"read": {"user_ids": ["reader"]}}),
            (User("public-reader"), None),
        )
        SpecState.user_valves_enabled = False
        for user, access_control in cases:
            with self.subTest(identity=user.id, role=user.role, has_user_valves=False):
                ToolStore.row.access_control = access_control
                ToolStore.user_valve_updates = []
                SpecState.loader_calls = 0
                cache = ProbeCache()
                request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
                with self.assertRaises(HTTPError) as caught:
                    await update_user_valves_route(request, "tool-shared", {}, user)
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.detail, "not found")
                self.assertEqual(SpecState.loader_calls, 1)
                self.assertIn("tool-shared", cache)
                self.assertEqual(ToolStore.user_valve_updates, [])

        SpecState.user_valves_enabled = True
        try:
            ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}
            ToolStore.user_valve_updates = []
            ToolStore.user_valve_values = {}
            ToolStore.config_reads = 0
            cache = ProbeCache()
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(TOOLS=cache)))
            first = await update_user_valves_route(
                request,
                "tool-shared",
                {"theme": "blue", "unset": None},
                User("reader"),
            )
            ToolStore.row.access_control = None
            second = await update_user_valves_route(
                request,
                "tool-shared",
                {"theme": "green"},
                User("public-reader"),
            )
            self.assertEqual(first, {"theme": "blue"})
            self.assertEqual(second, {"theme": "green"})
            self.assertEqual(
                ToolStore.user_valve_updates,
                [
                    ("tool-shared", "reader", {"theme": "blue"}),
                    ("tool-shared", "public-reader", {"theme": "green"}),
                ],
            )
            self.assertEqual(ToolStore.user_valve_values["tool-shared", "reader"], {"theme": "blue"})
            self.assertEqual(ToolStore.user_valve_values["tool-shared", "public-reader"], {"theme": "green"})
            self.assertEqual(ToolStore.config_reads, 0)
        finally:
            SpecState.user_valves_enabled = False
            ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}

    async def test_tool_list_uses_read_visible_identities_without_source(self):
        cases = (
            (User("owner"), {"private", "targeted", "public"}),
            (User("reader"), {"targeted", "public"}),
            (User("unrelated"), {"public"}),
            (User("administrator", "admin"), {"private", "targeted", "public"}),
        )
        ToolListStore.requested_permissions = []
        for user, expected_ids in cases:
            with self.subTest(identity=user.id, role=user.role):
                responses = await tool_list_route(user)
                self.assertEqual({response["id"] for response in responses}, expected_ids)
                for response in responses:
                    self.assertIsNone(response["content"])
                    self.assertEqual(response["specs"], [{"name": "run"}])
                    self.assertEqual(response["meta"]["description"], "synthetic metadata")
                    self.assertIsNone(response["user"])
        self.assertEqual(ToolListStore.requested_permissions, ["read", "read", "read"])

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
        self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")
        self.assertEqual(ModelStore.row.params, before)

    async def test_unrelated_private_tool_read_uses_access_prohibited_copy(self):
        ToolStore.row.access_control = {"read": {"user_ids": ["reader"]}}
        with self.assertRaises(HTTPError) as caught:
            await tool_by_id_route("tool-shared", User("unrelated"))
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.detail, "You do not have permission to access this resource. Please contact your administrator for assistance.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
