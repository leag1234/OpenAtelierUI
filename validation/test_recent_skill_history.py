#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for recent Skills file and version compatibility."""

from __future__ import annotations

import ast
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(os.environ.get("OPENATELIER_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
HELPER_PATH = ROOT / "backend/open_webui/utils/version_history.py"
SPEC = importlib.util.spec_from_file_location("_skill_history_test_helper", HELPER_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def declared_routes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in {"get", "post", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.add((decorator.func.attr.upper(), decorator.args[0].value))
    return routes


class RecentSkillHistoryTests(unittest.TestCase):
    def test_skill_file_history_lifecycle(self):
        users = {"owner": SimpleNamespace(name="Synthetic Owner")}
        lookup = users.get
        with tempfile.TemporaryDirectory(prefix="skill-history-") as directory:
            root = Path(directory)
            first = HELPER.append_version(
                root, "skill", "workshop", "owner",
                {"name": "One", "content": "# One", "data": {"files": [{"path": "SKILL.md", "content": "# One"}, {"path": "references/guide.md", "content": "first"}]}, "meta": {}},
                "owner", "Initial",
            )
            second = HELPER.append_version(
                root, "skill", "workshop", "owner",
                {"name": "Two", "content": "# Two", "data": {"files": [{"path": "SKILL.md", "content": "# Two"}, {"path": "references/guide.md", "content": "second"}]}, "meta": {}},
                "owner", "Updated",
            )
            rows = HELPER.list_versions(root, "skill", "workshop", "owner", 1, lookup)
            self.assertEqual({row["id"] for row in rows}, {first, second})
            self.assertEqual(rows[0]["skill_id"], "workshop")
            diff = HELPER.compare_version_files(root, "skill", "workshop", "owner", first, second, "references/guide.md", lookup)
            self.assertIn("-first", diff["content_diff"])
            self.assertIn("+second", diff["content_diff"])
            HELPER.select_version(root, "skill", "workshop", "owner", first)
            self.assertTrue(HELPER.delete_version(root, "skill", "workshop", "owner", second))

    def test_all_twelve_recent_skill_routes_are_declared(self):
        routes = declared_routes(ROOT / "backend/open_webui/routers/skills.py")
        expected = {
            ("GET", "/id/{id}/files"),
            ("GET", "/id/{id}/files/content"),
            ("GET", "/id/{id}/history"),
            ("GET", "/id/{id}/history/diff"),
            ("GET", "/id/{id}/history/diff/file"),
            ("GET", "/id/{id}/history/{history_id}"),
            ("DELETE", "/id/{id}/history/{history_id}"),
            ("POST", "/id/{id}/clone"),
            ("POST", "/id/{id}/update/version"),
            ("POST", "/import"),
            ("POST", "/import/preview"),
            ("POST", "/load/url"),
        }
        self.assertTrue(expected.issubset(routes), expected - routes)

    def test_skill_url_import_is_bounded_and_does_not_follow_redirects(self):
        source = (ROOT / "backend/open_webui/routers/skills.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("MAX_SKILL_IMPORT_BYTES = 16 * 1024 * 1024", source)
        self.assertIn("allow_redirects=False", source)
        self.assertIn("Private skill URLs are disabled", source)
        self.assertIn('suffix != ".md"', source)

    def test_forms_keep_optimistic_version_fields_required(self):
        tree = ast.parse((ROOT / "backend/open_webui/routers/skills.py").read_text(encoding="utf-8"))
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        clone_fields = {node.target.id for node in classes["CloneSkillForm"].body if isinstance(node, ast.AnnAssign)}
        version_fields = {node.target.id for node in classes["SkillVersionUpdateForm"].body if isinstance(node, ast.AnnAssign)}
        self.assertEqual(clone_fields, {"id", "name", "version_id"})
        self.assertEqual(version_fields, {"version_id", "expected_version_id"})


if __name__ == "__main__":
    unittest.main()
