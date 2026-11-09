# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for the measured memory organization compatibility contract."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/memories.py"
MODEL = ROOT / "backend/open_webui/models/memories.py"
MIGRATION = (
    ROOT
    / "backend/open_webui/migrations/versions/f17d1e2f3a4b_add_memory_organization.py"
)


class MemoryCompatibilityTests(unittest.TestCase):
    def test_five_measured_operations_are_exposed(self):
        tree = ast.parse(ROUTER.read_text())
        expected = {
            "search_memories": "/search",
            "list_memory_paths": "/paths",
            "read_memory_path": "/path",
            "update_memories": "/update",
            "reindex_memories_from_vector_db": "/reindex",
        }
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in expected
        }
        self.assertEqual(set(functions), set(expected))
        for name, route in expected.items():
            decorator = functions[name].decorator_list[0]
            self.assertEqual(ast.literal_eval(decorator.args[0]), route)

    def test_forms_match_the_measured_openapi_defaults(self):
        source = ROUTER.read_text()
        for fragment in (
            'action: Literal["add", "replace", "remove", "move"]',
            'source: Optional[Literal["tool", "background_review"]] = None',
            'type: MemoryTypeFilter = "all"',
            "limit: int = 20",
            "include_children: bool = True",
            "limit: int = 50",
            "limit: int = 100",
        ):
            self.assertIn(fragment, source)

    def test_owner_isolation_and_admin_only_reindex_are_explicit(self):
        tree = ast.parse(ROUTER.read_text())
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        for name in (
            "search_memories",
            "list_memory_paths",
            "read_memory_path",
            "update_memories",
        ):
            self.assertIn("get_verified_user", ast.unparse(functions[name].args))
        self.assertIn(
            "get_admin_user",
            ast.unparse(functions["reindex_memories_from_vector_db"].args),
        )
        source = ROUTER.read_text()
        self.assertIn("Memories.get_memory_by_id_and_user_id(operation.id, user.id)", source)
        self.assertIn('collection_name=f"user-memory-{user.id}"', source)

    def test_batch_and_result_bounds_are_explicit(self):
        source = ROUTER.read_text()
        self.assertIn("MAX_MEMORY_RESULTS = 1000", source)
        self.assertIn("MAX_MEMORY_OPERATIONS = 100", source)
        self.assertIn("Too many memory operations", source)
        self.assertIn("A copied database can legitimately have no vector collection yet", source)

    def test_persistent_fields_and_upgrade_are_present(self):
        model = MODEL.read_text()
        migration = MIGRATION.read_text()
        for fragment in (
            "path = Column(Text, nullable=True)",
            'type = Column(String, nullable=False, default="context")',
            "meta = Column(JSONField, nullable=True)",
            'type: Literal["user", "context"] = "context"',
        ):
            self.assertIn(fragment, model)
        self.assertIn('down_revision = "f16c0d3e4f5a"', migration)
        for column in ('"path"', '"type"', '"meta"'):
            self.assertIn(column, migration)


if __name__ == "__main__":
    unittest.main(verbosity=2)
