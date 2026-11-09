#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for signed-in user file count and search compatibility."""

from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path


ROOT = Path(
    os.environ.get("OPENATELIER_SOURCE_ROOT", Path(__file__).resolve().parents[1])
)
ROUTER = ROOT / "backend/open_webui/routers/files.py"


def route_function(name: str):
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"), filename=str(ROUTER))
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


class RecentUserFileSearchTests(unittest.TestCase):
    def test_recent_routes_are_declared_with_exact_paths(self):
        count = route_function("count_user_files")
        search = route_function("search_user_files")
        self.assertEqual(count.decorator_list[0].args[0].value, "/user/count")
        self.assertEqual(search.decorator_list[0].args[0].value, "/user/search")

    def test_count_is_always_scoped_to_signed_in_user(self):
        source = ast.unparse(route_function("count_user_files"))
        self.assertIn("Files.count_files(user.id)", source)
        self.assertNotIn("user.role", source)

    def test_search_has_bounded_pagination_and_owner_scope(self):
        source = ast.unparse(route_function("search_user_files"))
        self.assertIn("Files.get_files_by_user_id(user.id)", source)
        self.assertIn("Query(0, ge=0)", source)
        self.assertIn("Query(50, ge=1, le=1000)", source)
        self.assertIn("[skip:skip + limit]", source)
        self.assertNotIn("Files.get_files()", source)
        self.assertNotIn("HTTPException", source)

    def test_content_is_removed_from_a_copy_only(self):
        source = ast.unparse(route_function("search_user_files"))
        self.assertIn("values['data'] = dict(data)", source)
        self.assertIn("values['data'].pop('content', None)", source)
        self.assertNotIn("del file.data", source)


if __name__ == "__main__":
    unittest.main()
