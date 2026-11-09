#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for the bounded recent version-history compatibility slice."""

from __future__ import annotations

import ast
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(
    os.environ.get("OPENATELIER_SOURCE_ROOT", Path(__file__).resolve().parents[1])
)
HELPER_PATH = ROOT / "backend/open_webui/utils/version_history.py"
SPEC = importlib.util.spec_from_file_location("_version_history_test_helper", HELPER_PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def declared_routes(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if decorator.func.attr not in {"get", "post", "delete"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                routes.add((decorator.func.attr.upper(), decorator.args[0].value))
    source = path.read_text(encoding="utf-8")
    for match in (
        ("GET", '"/id/{id}/history/{history_id}"'),
    ):
        if match[1] in source and "add_api_route" in source:
            routes.add((match[0], match[1][1:-1]))
    return routes


class RecentVersionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="version-history-")
        self.root = Path(self.temp.name)
        self.users = {
            "owner": SimpleNamespace(name="Synthetic Owner"),
            "editor": SimpleNamespace(name="Synthetic Editor"),
        }

    def tearDown(self):
        self.temp.cleanup()

    def lookup(self, user_id):
        return self.users.get(user_id)

    def test_full_bounded_lifecycle_survives_reopen(self):
        first = HELPER.append_version(
            self.root,
            "tool",
            "synthetic_tool",
            "owner",
            {"name": "One", "content": "return 'one'\n", "meta": {}},
            "owner",
            "Initial synthetic version",
        )
        self.assertEqual(
            HELPER.ensure_root(
                self.root,
                "tool",
                "synthetic_tool",
                "owner",
                {"name": "ignored", "content": "ignored", "meta": {}},
                "owner",
            ),
            first,
        )
        second = HELPER.append_version(
            self.root,
            "tool",
            "synthetic_tool",
            "owner",
            {"name": "Two", "content": "return 'two'\n", "meta": {}},
            "editor",
            "Second synthetic version",
        )
        rows = HELPER.list_versions(
            self.root, "tool", "synthetic_tool", "owner", 1, self.lookup
        )
        self.assertEqual({row["id"] for row in rows}, {first, second})
        self.assertEqual(
            set(rows[0]),
            {
                "id",
                "tool_id",
                "parent_id",
                "user_id",
                "commit_message",
                "created_at",
                "user",
            },
        )
        detail = HELPER.get_version(
            self.root, "tool", "synthetic_tool", "owner", first, self.lookup
        )
        self.assertEqual(detail["snapshot"]["name"], "One")
        diff = HELPER.compare_versions(
            self.root,
            "tool",
            "synthetic_tool",
            "owner",
            first,
            second,
            self.lookup,
        )
        self.assertIn("-return 'one'", diff["content_diff"])
        self.assertEqual(diff["metadata"]["name"], {"before": "One", "after": "Two"})
        HELPER.select_version(self.root, "tool", "synthetic_tool", "owner", first)
        self.assertEqual(
            HELPER.current_version_id(
                self.root, "tool", "synthetic_tool", "owner"
            ),
            first,
        )
        self.assertTrue(
            HELPER.delete_version(
                self.root, "tool", "synthetic_tool", "owner", second
            )
        )
        reopened = HELPER.list_versions(
            self.root, "tool", "synthetic_tool", "owner", 1, self.lookup
        )
        self.assertEqual([row["id"] for row in reopened], [first])
        mode = self.root.joinpath("version-history.sqlite").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_invalid_owner_active_delete_and_oversized_snapshot_refuse(self):
        version_id = HELPER.append_version(
            self.root,
            "model",
            "synthetic-model",
            "owner",
            {"name": "One", "content": "", "meta": {}},
            "owner",
        )
        with self.assertRaises(HELPER.VersionHistoryUnavailable):
            HELPER.current_version_id(
                self.root, "model", "synthetic-model", "other"
            )
        with self.assertRaises(HELPER.VersionHistoryUnavailable):
            HELPER.delete_version(
                self.root, "model", "synthetic-model", "owner", version_id
            )
        with self.assertRaises(HELPER.VersionHistoryUnavailable):
            HELPER.append_version(
                self.root,
                "model",
                "large",
                "owner",
                {"content": "x" * (HELPER.MAX_SNAPSHOT_BYTES + 1)},
                "owner",
            )
        connection = sqlite3.connect(self.root / "version-history.sqlite")
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM version_history").fetchone()[0],
            1,
        )
        connection.close()

    def test_recent_routes_and_forms_are_declared(self):
        functions = (ROOT / "backend/open_webui/routers/functions.py").read_text(
            encoding="utf-8"
        )
        models = declared_routes(ROOT / "backend/open_webui/routers/models.py")
        tools = declared_routes(ROOT / "backend/open_webui/routers/tools.py")
        self.assertIn('"/id/{id}/history/diff"', functions)
        self.assertIn('"/id/{id}/update/version"', functions)
        self.assertIn('"/id/{id}/history/{history_id}"', functions)
        self.assertIn(("DELETE", "/model/history/{history_id}"), models)
        self.assertIn(("POST", "/model/update/version"), models)
        self.assertIn(("GET", "/id/{id}/history/diff"), tools)
        self.assertIn(("DELETE", "/id/{id}/history/{history_id}"), tools)
        self.assertIn(("POST", "/id/{id}/update/version"), tools)


if __name__ == "__main__":
    unittest.main()
