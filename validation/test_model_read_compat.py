# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for the measured local model discovery read contract."""

import ast
import importlib.util
import pathlib
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER = ROOT / "backend/open_webui/utils/model_read_compat.py"
ROUTER = ROOT / "backend/open_webui/routers/models.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("model_read_compat", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model(identifier, name, tags, created_at):
    return types.SimpleNamespace(
        id=identifier,
        name=name,
        created_at=created_at,
        updated_at=created_at,
        meta=types.SimpleNamespace(model_dump=lambda: {"tags": tags}),
    )


class ModelReadCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper()
        cls.models = [
            model("private", "Private Base", [{"name": "base"}, {"name": "private"}], 1),
            model("member", "Member Model", [{"name": "shared"}, {"name": "member"}], 2),
            model("public", "Public Model", [{"name": "shared"}, {"name": "public"}], 3),
        ]

    def test_tags_are_unique_sorted_and_accept_legacy_strings(self):
        item = model("x", "X", [{"name": "z"}, "a", {"name": "z"}, {"bad": "ignored"}], 1)
        self.assertEqual(self.helper.model_tag_names(item), ["a", "z"])

    def test_query_and_tag_filters_apply_after_authorization(self):
        visible = self.models[1:]
        self.assertEqual(
            [item.id for item in self.helper.filter_models(visible, query="member")],
            ["member"],
        )
        self.assertEqual(
            [item.id for item in self.helper.filter_models(visible, tag="shared")],
            ["member", "public"],
        )
        self.assertEqual(self.helper.filter_models(visible, tag="private"), [])

    def test_order_and_pagination_are_bounded(self):
        ordered = self.helper.filter_models(
            self.models, order_by="name", direction="desc"
        )
        self.assertEqual([item.id for item in ordered], ["public", "private", "member"])
        self.assertEqual(self.helper.paginate(ordered, 2, size=2), [ordered[2]])
        self.assertEqual(self.helper.paginate(ordered, -1, size=2), ordered[:2])

    def test_local_version_identifier_is_stable_and_changes_on_update(self):
        first = types.SimpleNamespace(id="model", user_id="owner", updated_at=1)
        same = types.SimpleNamespace(id="model", user_id="owner", updated_at=1)
        changed = types.SimpleNamespace(id="model", user_id="owner", updated_at=2)
        self.assertEqual(
            self.helper.local_model_version_id(first),
            self.helper.local_model_version_id(same),
        )
        self.assertNotEqual(
            self.helper.local_model_version_id(first),
            self.helper.local_model_version_id(changed),
        )

    def test_all_four_measured_routes_are_declared(self):
        tree = ast.parse(ROUTER.read_text(encoding="utf-8"))
        paths = {
            decorator.args[0].value
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "get"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        }
        self.assertTrue(
            {"/base/tags", "/export", "/list", "/tags"}.issubset(paths)
        )

    def test_read_responses_do_not_expose_legacy_acl(self):
        source = (ROOT / "backend/open_webui/models/models.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        bases = {
            node.name: [ast.unparse(base) for base in node.bases]
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        }
        self.assertEqual(bases["ModelAccessResponse"], ["ModelReadResponse"])
        self.assertEqual(bases["ModelExportResponse"], ["ModelReadResponse"])
        read_response = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ModelReadResponse"
        )
        fields = {
            node.target.id
            for node in read_response.body
            if isinstance(node, ast.AnnAssign)
        }
        self.assertNotIn("access_control", fields)
        router_source = ROUTER.read_text(encoding="utf-8")
        self.assertIn('response["meta"].pop("profile_image_url", None)', router_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
