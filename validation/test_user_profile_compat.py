# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for the measured local user-profile compatibility family."""

import ast
import importlib.util
import pathlib
import unittest
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "backend/open_webui/utils/user_profile_compat.py"
SPEC = importlib.util.spec_from_file_location("user_profile_compat_under_test", HELPER_PATH)
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


class UserProfileCompatibilityTests(unittest.TestCase):
    def user(self, **updates):
        values = {
            "id": "user-01", "name": "Community member", "email": "member@example.invalid",
            "role": "user", "profile_image_url": "/user.png", "last_active_at": 12,
            "updated_at": 11, "created_at": 10, "settings": {"timezone": "Europe/Paris"},
            "info": {"status_message": "Available"}, "api_key": "must-not-leak",
        }
        values.update(updates)
        return SimpleNamespace(**values)

    def test_status_projection_is_complete_and_excludes_credentials(self):
        projected = compat.status_projection(self.user())
        self.assertEqual(projected["status_message"], "Available")
        self.assertEqual(projected["timezone"], "Europe/Paris")
        self.assertIsNone(projected["oauth"])
        self.assertNotIn("api_key", projected)

    def test_status_update_preserves_unrelated_info(self):
        updated = compat.update_status_info(
            {"bio": "Workshop organiser"},
            compat.UserStatus(status_emoji="🧪", status_message="Testing", status_expires_at=123),
        )
        self.assertEqual(updated["bio"], "Workshop organiser")
        self.assertEqual(updated["status_message"], "Testing")

    def test_variables_only_allow_preconfigured_existing_names(self):
        self.assertEqual(
            compat.validate_variables({"USER_LOCATION": "Paris"}, {"USER_LOCATION": "Lyon"}),
            {"USER_LOCATION": "Paris"},
        )
        with self.assertRaisesRegex(ValueError, "Invalid user variable key: USER_LOCATION"):
            compat.validate_variables({"USER_LOCATION": "Paris"}, {})
        with self.assertRaisesRegex(ValueError, "Invalid user variable key: COMMUNITY"):
            compat.validate_variables({"COMMUNITY": "workshop"}, {})
        info = compat.update_variables_info({"status_message": "Ready"}, {"USER_LOCATION": "Paris"})
        self.assertEqual(compat.read_variables(self.user(info=info)), {"USER_LOCATION": "Paris"})

    def test_internal_storage_is_not_exposed_through_public_info(self):
        user = self.user(info={"bio": "Workshop", "status_message": "Ready", "_openatelier_user_variables": {}})
        self.assertEqual(compat.public_info(user), {"bio": "Workshop"})
        with self.assertRaisesRegex(ValueError, "Reserved user info field"):
            compat.merge_public_info(user.info, {"status_message": "overwrite"})

    def test_empty_usage_matches_seven_day_response_shape(self):
        usage = compat.build_usage([], 7, now=1_800_000_000)
        self.assertEqual(usage.period.days, 7)
        self.assertEqual(len(usage.heatmap), 7)
        self.assertEqual(len(usage.cumulative_heatmap), 7)
        self.assertEqual(usage.totals.messages, 0)
        self.assertEqual(usage.top_models, [])

    def test_usage_counts_local_messages_and_tokens(self):
        now = 1_800_000_000
        chat = SimpleNamespace(
            created_at=now - 60,
            updated_at=now,
            chat={
                "history": {
                    "messages": {
                        "u": {"role": "user", "timestamp": now - 50, "model": "local-model"},
                        "a": {
                            "role": "assistant", "timestamp": now - 40, "model": "local-model",
                            "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                        },
                    }
                }
            },
        )
        usage = compat.build_usage([chat], 7, now=now)
        self.assertEqual((usage.totals.messages, usage.totals.total_chats), (2, 1))
        self.assertEqual((usage.totals.input_tokens, usage.totals.output_tokens), (3, 5))
        self.assertEqual(usage.top_models[0].model_id, "local-model")

    def test_all_twelve_routes_keep_measured_auth_dependencies(self):
        tree = ast.parse((ROOT / "backend/open_webui/routers/users.py").read_text())
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
        }
        verified = {
            "get_user_usage_by_session_user", "get_user_status_by_session_user",
            "update_user_status_by_session_user", "get_user_variables_by_session_user",
            "update_user_variables_by_session_user", "get_user_active_status_by_id",
            "get_user_info_by_id", "get_user_profile_image_by_id",
        }
        admin = {
            "get_default_user_permissions_defaults", "get_user_groups_by_id",
            "get_user_oauth_sessions_by_id", "get_user_preview",
        }
        self.assertEqual(verified | admin, set(verified | admin) & set(functions))
        for name in verified:
            self.assertIn("get_verified_user", ast.unparse(functions[name].args))
        for name in admin:
            self.assertIn("get_admin_user", ast.unparse(functions[name].args))

    def test_legacy_group_update_does_not_mutate_membership(self):
        tree = ast.parse((ROOT / "backend/open_webui/models/groups.py").read_text())
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "update_group_by_id"
        )
        source = ast.unparse(method)
        self.assertIn("exclude={'user_ids'}", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
