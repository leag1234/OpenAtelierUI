# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for the measured analytics-session projections."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "backend/open_webui/utils/analytics_compat.py").is_file():
    ROOT = Path("/app")
SPEC = importlib.util.spec_from_file_location(
    "analytics_sessions_compat_under_test",
    ROOT / "backend/open_webui/utils/analytics_compat.py",
)
analytics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analytics)


class AnalyticsSessionCompatibilityTests(unittest.TestCase):
    def setUp(self):
        question = {
            "id": "question", "parentId": None, "role": "user",
            "content": "Community workshop question", "timestamp": 100,
        }
        answer = {
            "id": "answer", "parentId": "question", "role": "assistant",
            "content": "Synthetic answer", "timestamp": 104, "model": "local-model",
            "done": True, "usage": {"prompt_tokens": 7, "completion_tokens": 11, "total_tokens": 18},
            "output": [{
                "type": "message", "status": "completed",
                "content": [{"type": "output_text", "text": "Synthetic answer"}],
            }],
        }
        self.chat = SimpleNamespace(
            id="chat", user_id="owner", folder_id="folder", title="Community workshop",
            updated_at=105, chat={"history": {"messages": {"q": question, "a": answer}}},
        )
        self.user = SimpleNamespace(name="Owner")
        self.folder = SimpleNamespace(name="Workshop folder")

    def test_session_summary_and_overview(self):
        record = analytics.session_record(
            self.chat, lambda _user_id: self.user, lambda _folder_id: self.folder
        )
        self.assertEqual(record["tokens"], 18)
        self.assertEqual(record["messages"], 2)
        self.assertEqual(record["prompts"], 1)
        self.assertEqual(record["responses"], 1)
        self.assertEqual(record["span_seconds"], 4)
        self.assertEqual(record["model_id"], "local-model")
        self.assertEqual(record["folder_name"], "Workshop folder")
        overview = analytics.session_overview([record])
        self.assertEqual(overview["token_distribution"], [0, 1, 0, 0, 0, 0])
        self.assertEqual(overview["unrecorded_sessions"], 0)

    def test_messages_require_composite_event_identifier(self):
        messages = analytics.session_message_rows(self.chat)
        self.assertEqual(messages[1]["id"], "chat-answer")
        self.assertEqual(messages[1]["output_count"], 1)
        self.assertIsNone(analytics.session_event_rows(self.chat, "answer"))
        events = analytics.session_event_rows(self.chat, "chat-answer")
        self.assertEqual(events, [{
            "type": "message", "status": "completed", "name": None,
            "call_id": None, "started_at": None, "ended_at": None,
            "duration_seconds": None, "error_type": None,
            "details": '[{"type":"output_text","text":"Synthetic answer"}]',
            "truncated": True, "index": 0,
        }])

    def test_trace_and_chart_include_response_and_output(self):
        trace = analytics.session_trace_rows(self.chat)
        self.assertEqual([row["type"] for row in trace], ["response", "message"])
        self.assertEqual(analytics.session_trace_rows(self.chat, view="tools"), [])
        chart = analytics.trace_chart(self.chat, trace)
        self.assertEqual(chart["timed_operations"], 0)
        self.assertEqual(chart["usage"][0]["input_tokens"], 7)
        self.assertEqual(chart["usage"][0]["output_tokens"], 11)
        self.assertEqual(chart["usage"][0]["first"], 4)

    def test_date_and_group_inputs_filter_sessions(self):
        self.assertEqual(
            analytics.session_records([self.chat], lambda _: self.user, start_date=105), []
        )
        self.assertEqual(
            analytics.session_records([self.chat], lambda _: self.user, user_ids=["other"]), []
        )

    def test_message_errors_are_stable_json_strings(self):
        self.chat.chat["history"]["messages"]["a"]["error"] = {"detail": "Synthetic failure"}
        row = analytics.session_message_rows(self.chat)[1]
        self.assertEqual(row["error"], '{"detail":"Synthetic failure"}')


if __name__ == "__main__":
    unittest.main()
