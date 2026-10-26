#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted checks for the owned plugin callback request path."""

from __future__ import annotations

import ast
import asyncio
import os
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "backend/open_webui/socket/main.py"


class CountingPool(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups = []

    def get(self, key, default=None):
        self.lookups.append(key)
        return super().get(key, default)


class RecordingSocket:
    def __init__(self):
        self.calls = []

    async def call(self, event, payload, to=None):
        result = {"ack": "synthetic"}
        self.calls.append((event, payload, to, result))
        return result


def load_get_event_call(session_pool, socket):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_event_call"
    )
    alias = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "get_event_caller" for target in node.targets)
    )
    module = ast.Module(body=[function, alias], type_ignores=[])
    namespace = {"SESSION_POOL": session_pool, "sio": socket, "os": os}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace["get_event_call"], namespace["get_event_caller"]


class PluginCallbackOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.pop("OPENATELIER_EVENT_CHANNEL_COMPAT", None)
        self.sessions = CountingPool({"member-session": {"id": "member"}})
        self.socket = RecordingSocket()
        self.get_event_call, self.get_event_caller = load_get_event_call(
            self.sessions, self.socket
        )

    def tearDown(self):
        os.environ.pop("OPENATELIER_EVENT_CHANNEL_COMPAT", None)
        if self.previous_flag is not None:
            os.environ["OPENATELIER_EVENT_CHANNEL_COMPAT"] = self.previous_flag

    def test_owned_callback_preserves_payload_result_and_single_channel(self):
        request = {
            "user_id": "member",
            "session_id": "member-session",
            "chat_id": "local:synthetic-chat",
            "message_id": "local:synthetic-message",
        }
        event = {"type": "synthetic-callback", "data": {"value": "marker"}}
        callback = self.get_event_call(request)
        result = asyncio.run(callback(event))
        self.assertEqual(result, {"ack": "synthetic"})
        self.assertEqual(len(self.socket.calls), 1)
        self.assertEqual(
            self.socket.calls[0],
            (
                "chat-events",
                {
                    "chat_id": "local:synthetic-chat",
                    "message_id": "local:synthetic-message",
                    "data": event,
                },
                "member-session",
                {"ack": "synthetic"},
            ),
        )
        self.assertIs(self.socket.calls[0][1]["data"], event)

    def test_exact_opt_in_uses_one_events_call_and_alias_is_preserved(self):
        os.environ["OPENATELIER_EVENT_CHANNEL_COMPAT"] = "1"
        request = {"user_id": "member", "session_id": "member-session"}
        self.assertIs(self.get_event_caller, self.get_event_call)
        result = asyncio.run(self.get_event_caller(request)({"type": "callback"}))
        self.assertEqual(result, {"ack": "synthetic"})
        self.assertEqual(len(self.socket.calls), 1)
        self.assertEqual(self.socket.calls[0][0], "events")

        os.environ["OPENATELIER_EVENT_CHANNEL_COMPAT"] = "true"
        asyncio.run(self.get_event_call(request)({"type": "callback"}))
        self.assertEqual(len(self.socket.calls), 2)
        self.assertEqual(self.socket.calls[1][0], "chat-events")

    def test_factory_is_lazy_and_checks_current_session_owner_at_call_time(self):
        callback = self.get_event_call({"chat_id": "local:ordinary", "session_id": "stale"})
        self.assertEqual(self.sessions.lookups, [])
        self.assertEqual(self.socket.calls, [])

        request = {"user_id": "member", "session_id": "member-session"}
        callback = self.get_event_call(request)
        self.sessions["member-session"] = {"id": "other"}
        with self.assertRaisesRegex(ValueError, "^Callback session unavailable$"):
            asyncio.run(callback({"type": "callback"}))
        self.assertEqual(self.socket.calls, [])
        self.assertEqual(self.sessions.lookups, ["member-session"])

    def test_missing_foreign_and_malformed_identity_refuse_before_transport(self):
        cases = (
            None,
            [],
            {},
            {"user_id": "", "session_id": "member-session"},
            {"user_id": None, "session_id": "member-session"},
            {"user_id": "member", "session_id": ""},
            {"user_id": "member", "session_id": 7},
            {"user_id": "other", "session_id": "member-session"},
            {"user_id": "member", "session_id": "unknown"},
        )
        for request in cases:
            with self.subTest(request_type=type(request).__name__):
                callback = self.get_event_call(request)
                with self.assertRaisesRegex(ValueError, "^Callback session unavailable$"):
                    asyncio.run(callback({"type": "callback"}))
        self.assertEqual(self.socket.calls, [])

    def test_malformed_pool_entries_fail_closed_without_identifiers_in_error(self):
        for entry in (None, [], {}, {"other": "member"}, {"id": None}):
            with self.subTest(entry_type=type(entry).__name__):
                self.sessions["member-session"] = entry
                callback = self.get_event_call(
                    {"user_id": "member", "session_id": "member-session"}
                )
                with self.assertRaisesRegex(ValueError, "^Callback session unavailable$") as raised:
                    asyncio.run(callback({"type": "callback"}))
                self.assertNotIn("member", str(raised.exception))
                self.assertNotIn("member-session", str(raised.exception))
        self.assertEqual(self.socket.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
