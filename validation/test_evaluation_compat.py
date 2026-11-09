# SPDX-License-Identifier: BSD-3-Clause
"""Focused checks for provider-free evaluation projections."""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "backend/open_webui/utils/evaluation_compat.py"
SPEC = importlib.util.spec_from_file_location("evaluation_compat_under_test", PATH)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvaluationCompatibilityTests(unittest.TestCase):
    def feedbacks(self):
        return [
            SimpleNamespace(id="1", user_id="u", version=0, type="rating", data={"model_id": "a", "rating": 1, "sibling_model_ids": ["b"]}, meta={}, created_at=100, updated_at=100),
            SimpleNamespace(id="2", user_id="u", version=0, type="rating", data={"model_id": "a", "rating": -1, "sibling_model_ids": ["b"]}, meta={}, created_at=101, updated_at=101),
            SimpleNamespace(id="3", user_id="u", version=0, type="rating", data={"model_id": "b", "rating": 1, "sibling_model_ids": ["c"]}, meta={}, created_at=102, updated_at=102),
            SimpleNamespace(id="4", user_id="u", version=0, type="rating", data={"model_id": "c", "rating": 1, "sibling_model_ids": ["a"]}, meta={}, created_at=103, updated_at=103),
        ]

    def test_measured_elo_sequence(self):
        self.assertEqual(evaluation.leaderboard(self.feedbacks()), {"entries": [
            {"model_id": "b", "rating": 1017, "won": 2, "lost": 1, "count": 3, "top_tags": []},
            {"model_id": "c", "rating": 1001, "won": 1, "lost": 1, "count": 2, "top_tags": []},
            {"model_id": "a", "rating": 982, "won": 1, "lost": 2, "count": 3, "top_tags": []},
        ]})

    def test_history_counts_only_direct_ratings(self):
        rows = [
            SimpleNamespace(id="1", data={"model_id": "a", "rating": 1}, created_at=1794217131),
            SimpleNamespace(id="2", data={"model_id": "a", "rating": -1}, created_at=1794217132),
            SimpleNamespace(id="3", data={"model_id": "b", "rating": 1, "sibling_model_ids": ["a"]}, created_at=1794217133),
        ]
        now = datetime(2026, 11, 9, tzinfo=timezone.utc)
        result = evaluation.model_history(rows, "a", 1, now=now)
        self.assertEqual(result["history"], [{"date": "2026-11-09", "won": 1, "lost": 1}])

    def test_list_filters_paginates_and_redacts_profile(self):
        user = SimpleNamespace(id="u", name="Member", email="member@example.invalid", role="user", last_active_at=3, updated_at=2, created_at=1, api_key="secret")
        result = evaluation.list_projection(self.feedbacks(), lambda _: user, model_id="a", direction="asc")
        self.assertEqual(result["total"], 2)
        self.assertNotIn("api_key", result["items"][0]["user"])

    def test_ids_and_models_are_deterministic(self):
        self.assertEqual(evaluation.model_ids(self.feedbacks()), ["a", "b", "c"])
        self.assertEqual([row["id"] for row in evaluation.id_projection(self.feedbacks())], ["1", "2", "3", "4"])

    def test_nonpositive_history_window_is_empty(self):
        self.assertEqual(evaluation.model_history(self.feedbacks(), "a", -1), {"model_id": "a", "history": []})


if __name__ == "__main__":
    unittest.main()
