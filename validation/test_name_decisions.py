#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Check that internal provenance renames preserve HTTP-facing identifiers."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def route_functions(relative_path: str):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def route_metadata(function):
    decorator = next(
        item
        for item in function.decorator_list
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
    )
    path = ast.literal_eval(decorator.args[0])
    keywords = {
        item.arg: ast.literal_eval(item.value)
        for item in decorator.keywords
        if item.arg == "operation_id"
    }
    parameters = [argument.arg for argument in function.args.args]
    return path, keywords.get("operation_id"), parameters


class NameDecisionTests(unittest.TestCase):
    def test_notes_internal_rename_preserves_wire_names(self):
        functions = route_functions("backend/open_webui/routers/notes.py")
        path, operation_id, parameters = route_metadata(functions["query_private_notes"])
        self.assertEqual(path, "/search")
        self.assertEqual(operation_id, "search_notes_api_v1_notes_search_get")
        self.assertEqual(
            parameters,
            ["request", "query", "view_option", "permission", "order_by", "direction", "page", "user"],
        )

    def test_model_internal_renames_pin_previous_operation_ids(self):
        functions = route_functions("backend/open_webui/routers/models.py")
        expected = {
            "list_imported_model_versions": (
                "/model/history",
                "get_model_history_api_v1_models_model_history_get",
                ["id", "page", "user"],
            ),
            "read_imported_model_version": (
                "/model/history/{version_id}",
                "get_model_history_version_api_v1_models_model_history__version_id__get",
                ["id", "version_id", "user"],
            ),
            "apply_model_access_grants": (
                "/model/access/update",
                "update_model_access_by_id_api_v1_models_model_access_update_post",
                ["form_data", "user"],
            ),
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(route_metadata(functions[name]), value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
