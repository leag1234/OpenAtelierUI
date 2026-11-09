# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for provider-free local analytics."""

import importlib.util
import pathlib
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "backend/open_webui/utils/analytics_compat.py"
SPEC = importlib.util.spec_from_file_location("analytics_compat_under_test", PATH)
analytics = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(analytics)

class AnalyticsCompatibilityTests(unittest.TestCase):
    def chats(self):
        messages = {
            "q": {"id": "q", "parentId": None, "role": "user", "content": "Question", "timestamp": 100},
            "a": {"id": "a", "parentId": "q", "role": "assistant", "content": "Answer", "timestamp": 101, "model": "local-a", "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}},
        }
        return [SimpleNamespace(id="chat-1", user_id="user-1", title="Workshop", chat={"history": {"messages": messages}}, updated_at=110)]

    def test_summary_models_and_tokens_use_assistant_records(self):
        records = analytics.assistant_records(self.chats())
        self.assertEqual(analytics.summary(records), {"total_messages": 1, "total_chats": 1, "total_models": 1, "total_users": 1})
        self.assertEqual(analytics.model_analytics(records)[0]["count"], 1)
        self.assertEqual(analytics.token_analytics(records)["total_tokens"], 8)

    def test_messages_require_a_filter_and_normalize_usage(self):
        self.assertEqual(analytics.normalized_messages(self.chats()), [])
        rows = analytics.normalized_messages(self.chats(), chat_id="chat-1")
        self.assertEqual([row["role"] for row in rows], ["user", "assistant"])
        self.assertEqual(rows[1]["usage"]["input_tokens"], 3)
        self.assertEqual(rows[1]["usage"]["output_tokens"], 5)

    def test_current_additive_daily_shape(self):
        row = analytics.daily_analytics(analytics.assistant_records(self.chats()), "daily")[0]
        self.assertEqual(set(row), {"date", "models", "start", "end", "previous"})
        self.assertEqual(row["models"], {"local-a": 1})

    def test_user_and_model_chat_projection(self):
        records = analytics.assistant_records(self.chats())
        user = SimpleNamespace(name="Member", email="member@example.invalid")
        self.assertEqual(analytics.user_analytics(records, lambda _: user)[0]["total_tokens"], 8)
        self.assertEqual(analytics.model_chats(self.chats(), "local-a", user_lookup=lambda _: user)[0]["first_message"], "Workshop")

if __name__ == "__main__": unittest.main(verbosity=2)
