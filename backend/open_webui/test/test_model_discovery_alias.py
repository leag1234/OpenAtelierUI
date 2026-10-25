# SPDX-License-Identifier: BSD-3-Clause
"""Exercise the old callable-model aggregate and verify its route alias."""

import ast
import asyncio
import json
import logging
import pathlib
import types
import unittest
from typing import Optional


MAIN_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "main.py"


def load_get_models():
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_models"
    )
    module = ast.Module(body=[function], type_ignores=[])
    class SourceApp:
        def get(self, path):
            return lambda endpoint: endpoint

    namespace = {
        "app": SourceApp(),
        "Optional": Optional,
        "Request": object,
        "Depends": lambda dependency: dependency,
        "get_verified_user": object(),
        "json": json,
        "log": logging.getLogger("model-discovery-source-test"),
    }
    exec(compile(module, str(MAIN_SOURCE), "exec"), namespace)
    return function, namespace["get_models"]


def _source_route_paths(function):
    return {
        decorator.args[0].value
        for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
        and decorator.args
        and isinstance(decorator.args[0], ast.Constant)
    }


class ModelRecord:
    def __init__(self, user_id, access_control):
        self.user_id = user_id
        self.access_control = access_control


class ModelRegistry:
    def __init__(self, records):
        self.records = records

    def get_model_by_id(self, model_id):
        return self.records[model_id]


def has_access(user_id, type="read", access_control=None):
    if access_control is None:
        return type == "read"
    return user_id in access_control.get(type, {}).get("user_ids", [])


class ModelDiscoveryAliasTests(unittest.TestCase):
    def test_both_routes_use_the_same_existing_authenticated_handler(self):
        function, handler = load_get_models()
        self.assertEqual(
            _source_route_paths(function), {"/api/models", "/api/v1/models"}
        )
        self.assertIn("Depends(get_verified_user)", ast.unparse(function.args))
        refresh_argument = next(
            argument for argument in function.args.args if argument.arg == "refresh"
        )
        self.assertEqual(ast.unparse(refresh_argument.annotation), "Optional[bool]")
        self.assertTrue(asyncio.iscoroutinefunction(handler))

    def test_existing_aggregate_filters_private_targeted_public_and_pipeline_models(self):
        function, handler = load_get_models()
        self.assertIn("get_filtered_models", ast.unparse(function))
        self.assertIn("has_access", ast.unparse(function))
        names = ("owner_tool", "member_tool", "public_model", "private_model", "filter_pipe")
        entries = [
            {"id": name, "name": name, "info": {"meta": {"tags": []}}, "tags": []}
            for name in names
        ]
        entries[-1]["pipeline"] = {"type": "filter"}
        records = {
            "owner_tool": ModelRecord("owner", {}),
            "member_tool": ModelRecord("someone-else", {"read": {"user_ids": ["member"]}}),
            "public_model": ModelRecord("someone-else", None),
            "private_model": ModelRecord("someone-else", {}),
            "filter_pipe": ModelRecord("owner", {}),
        }
        namespace = handler.__globals__
        namespace.update(
            {
                "get_all_models": lambda request, user: asyncio.sleep(0, result=entries),
                "Models": ModelRegistry(records),
                "has_access": has_access,
                "BYPASS_MODEL_ACCESS_CONTROL": False,
            }
        )
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(
                    config=types.SimpleNamespace(MODEL_ORDER_LIST=[])
                )
            )
        )

        async def discover(user_id, role="user"):
            user = types.SimpleNamespace(id=user_id, role=role)
            return await handler(request=request, user=user)

        member = asyncio.run(discover("member"))
        unrelated = asyncio.run(discover("unrelated"))
        owner = asyncio.run(discover("owner"))
        admin = asyncio.run(discover("administrator", role="admin"))
        self.assertEqual(
            {item["id"] for item in member["data"]},
            {"member_tool", "public_model"},
        )
        self.assertEqual({item["id"] for item in unrelated["data"]}, {"public_model"})
        self.assertEqual(
            {item["id"] for item in owner["data"]},
            {"owner_tool", "public_model"},
        )
        self.assertEqual(
            {item["id"] for item in admin["data"]},
            {"owner_tool", "member_tool", "public_model", "private_model"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
