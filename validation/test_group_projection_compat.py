# SPDX-License-Identifier: BSD-3-Clause
"""Focused checks for measured flat-group projections."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "backend/open_webui/utils/group_compat.py"
SPEC = importlib.util.spec_from_file_location("group_compat_under_test", PATH)
groups = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(groups)


class GroupProjectionCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.group = SimpleNamespace(
            id="g1", user_id="admin", name="Group", description="Fixture",
            permissions={"read": {"group_ids": [], "user_ids": []}},
            data={"config": {"share": "members"}}, meta=None,
            user_ids=["u1", "u1", "u2"], created_at=1, updated_at=2,
        )

    def test_export_deduplicates_members_and_adds_parent(self):
        result = groups.project_group_export(self.group)
        self.assertEqual(result["user_ids"], ["u1", "u2"])
        self.assertEqual(result["member_count"], 2)
        self.assertIsNone(result["parent_group_id"])

    def test_info_does_not_expose_acl_or_members(self):
        result = groups.project_group_info(self.group)
        self.assertEqual(result["name"], "Group")
        self.assertNotIn("permissions", result)
        self.assertNotIn("user_ids", result)

    def test_preview_projects_only_group_scoped_resources(self):
        direct = {"read": {"group_ids": ["g1"]}}
        other = {"read": {"group_ids": ["g2"]}}
        result = groups.project_group_preview(
            self.group,
            {"features": {"notes": True}},
            models=[
                SimpleNamespace(id="m1", name="Model", access_control=direct),
                SimpleNamespace(id="m2", name="Other", access_control=other),
            ],
            tools=[SimpleNamespace(id="t1", name="Tool", access_control=direct)],
            knowledge=[],
        )
        self.assertEqual(result["models"], {"items": [{"id": "m1", "name": "Model"}], "total": 1})
        self.assertEqual(result["tools"]["total"], 1)
        self.assertTrue(result["effective_permissions"]["features"]["notes"])
        self.assertEqual(result["effective_permissions"]["read"]["group_ids"], [])

    def test_preview_does_not_mutate_default_permissions(self):
        defaults = {"features": {"notes": True}}
        groups.project_group_preview(self.group, defaults)
        self.assertEqual(defaults, {"features": {"notes": True}})

    def test_acl_group_match_accepts_read_or_write(self):
        self.assertTrue(groups.group_has_resource_access({"write": {"group_ids": ["g1"]}}, "g1"))
        self.assertFalse(groups.group_has_resource_access(None, "g1"))


if __name__ == "__main__":
    unittest.main()
