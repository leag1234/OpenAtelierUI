#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted controls for plugin event session ownership and aliasing."""

from __future__ import annotations

import ast
import asyncio
import os
import unittest
from pathlib import Path


SOCKET_SOURCE = Path(__file__).resolve().parents[1] / "backend/open_webui/socket/main.py"


class CountingPool(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lookups = []

    def get(self, key, default=None):
        self.lookups.append(key)
        return super().get(key, default)


class RecordingSocket:
    def __init__(self):
        self.emissions = []

    async def emit(self, channel, envelope, to=None):
        self.emissions.append((channel, envelope, to))


class RecordingChats:
    def __init__(self):
        self.status_updates = []
        self.messages = []
        self.replacements = []

    def add_message_status_to_chat_by_id_and_message_id(self, chat_id, message_id, data):
        self.status_updates.append((chat_id, message_id, data))

    def get_message_by_id_and_message_id(self, chat_id, message_id):
        return None

    def upsert_message_to_chat_by_id_and_message_id(self, chat_id, message_id, data):
        self.messages.append((chat_id, message_id, data))


def load_emitter(session_pool, user_pool, socket, chats):
    tree = ast.parse(SOCKET_SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_event_emitter"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "SESSION_POOL": session_pool,
        "USER_POOL": user_pool,
        "sio": socket,
        "Chats": chats,
        "os": os,
    }
    exec(compile(module, str(SOCKET_SOURCE), "exec"), namespace)
    return namespace["get_event_emitter"]


class EventEmitterOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.pop("OPENATELIER_EVENT_CHANNEL_COMPAT", None)
        self.session_pool = CountingPool({
            "own-a": {"id": "user-a"},
            "own-b": {"id": "user-a"},
            "foreign": {"id": "user-b"},
            "missing-id": {},
            "malformed": None,
        })
        self.user_pool = CountingPool({
            "user-a": ["own-a", "own-b", "foreign", "missing", "missing-id", "malformed", "own-a"]
        })
        self.socket = RecordingSocket()
        self.chats = RecordingChats()
        self.get_event_emitter = load_emitter(
            self.session_pool, self.user_pool, self.socket, self.chats
        )

    def tearDown(self):
        os.environ.pop("OPENATELIER_EVENT_CHANNEL_COMPAT", None)
        if self.previous_flag is not None:
            os.environ["OPENATELIER_EVENT_CHANNEL_COMPAT"] = self.previous_flag

    def test_foreign_and_malformed_sessions_are_filtered_with_legacy_channel_once(self):
        emitter = self.get_event_emitter({
            "user_id": "user-a",
            "session_id": "foreign",
            "chat_id": "local:synthetic-chat",
            "message_id": "local:synthetic-message",
        })
        event = {"type": "progress", "data": {"status": "working"}}
        asyncio.run(emitter(event))
        self.assertEqual(len(self.socket.emissions), 2)
        self.assertEqual({item[0] for item in self.socket.emissions}, {"chat-events"})
        self.assertEqual({item[2] for item in self.socket.emissions}, {"own-a", "own-b"})
        for _, envelope, _ in self.socket.emissions:
            self.assertEqual(envelope, {
                "chat_id": "local:synthetic-chat",
                "message_id": "local:synthetic-message",
                "data": event,
            })
            self.assertIs(envelope["data"], event)
        self.assertEqual(self.session_pool.lookups.count("own-a"), 1)
        self.assertEqual(self.session_pool.lookups.count("own-b"), 1)
        self.assertEqual(self.session_pool.lookups.count("foreign"), 1)

    def test_opt_in_alias_emits_same_envelope_once_on_each_channel(self):
        os.environ["OPENATELIER_EVENT_CHANNEL_COMPAT"] = "1"
        emitter = self.get_event_emitter({
            "user_id": "user-a",
            "session_id": "own-a",
            "chat_id": None,
            "message_id": None,
        })
        event = {"type": "message", "data": {"content": "synthetic reply"}}
        asyncio.run(emitter(event))
        self.assertEqual(len(self.socket.emissions), 4)
        for session_id in ("own-a", "own-b"):
            packets = [entry for entry in self.socket.emissions if entry[2] == session_id]
            self.assertEqual([entry[0] for entry in packets].count("chat-events"), 1)
            self.assertEqual([entry[0] for entry in packets].count("events"), 1)
            self.assertIs(packets[0][1], packets[1][1])
        self.assertEqual(self.chats.messages, [])

    def test_invalid_request_user_id_never_routes_or_reads_pools(self):
        for user_id in (None, "", 7, []):
            with self.subTest(user_id=user_id):
                self.socket.emissions.clear()
                self.session_pool.lookups.clear()
                self.user_pool.lookups.clear()
                emitter = self.get_event_emitter({"user_id": user_id, "session_id": "own-a"})
                asyncio.run(emitter({"type": "progress", "data": {}}))
                self.assertEqual(self.socket.emissions, [])
                self.assertEqual(self.session_pool.lookups, [])
                self.assertEqual(self.user_pool.lookups, [])

    def test_status_database_update_behavior_remains_one_call(self):
        emitter = self.get_event_emitter({
            "user_id": "user-a",
            "session_id": "own-a",
            "chat_id": "local:synthetic-chat",
            "message_id": "local:synthetic-message",
        })
        status = {"type": "status", "data": {"description": "working"}}
        asyncio.run(emitter(status))
        self.assertEqual(len(self.socket.emissions), 2)
        self.assertEqual(len(self.chats.status_updates), 1)
        self.assertEqual(
            self.chats.status_updates[0],
            ("local:synthetic-chat", "local:synthetic-message", status["data"]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
