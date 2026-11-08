# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise measured message mutation, compaction and statistics behavior."""

import ast
import asyncio
from collections import Counter
import copy
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest

from fastapi import HTTPException, status
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "backend/open_webui/routers/chats.py"


def extract(path, class_names=(), function_names=(), namespace=None):
    selected = []
    for node in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(node, ast.ClassDef) and node.name in class_names:
            selected.append(node)
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in function_names
        ):
            node.decorator_list = []
            selected.append(node)
    found = {node.name for node in selected}
    assert found == set(class_names) | set(function_names), found
    values = {} if namespace is None else namespace
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), values)
    return values


def fixture_chat():
    messages = {
        "q-root": {
            "id": "q-root",
            "parentId": None,
            "childrenIds": ["a-root"],
            "role": "user",
            "content": "Root question",
            "timestamp": 100,
        },
        "a-root": {
            "id": "a-root",
            "parentId": "q-root",
            "childrenIds": ["q-main", "q-alt"],
            "role": "assistant",
            "content": "Root answer",
            "timestamp": 102,
            "model": "model-a",
        },
        "q-main": {
            "id": "q-main",
            "parentId": "a-root",
            "childrenIds": ["a-main"],
            "role": "user",
            "content": "Main continuation",
            "timestamp": 110,
        },
        "a-main": {
            "id": "a-main",
            "parentId": "q-main",
            "childrenIds": [],
            "role": "assistant",
            "content": "Main answer",
            "timestamp": 114,
            "model": "model-a",
        },
        "q-alt": {
            "id": "q-alt",
            "parentId": "a-root",
            "childrenIds": ["a-alt"],
            "role": "user",
            "content": "Alternative continuation",
            "timestamp": 120,
        },
        "a-alt": {
            "id": "a-alt",
            "parentId": "q-alt",
            "childrenIds": [],
            "role": "assistant",
            "content": "Alternative answer",
            "timestamp": 125,
            "model": "model-b",
        },
    }
    return {
        "title": "Community workshop",
        "history": {"messages": messages, "currentId": "a-main"},
        "messages": [messages[key] for key in ("q-root", "a-root", "q-main", "a-main")],
        "models": ["model-a", "model-b"],
    }


class Store:
    def __init__(self):
        self.chat = SimpleNamespace(
            id="chat-a",
            user_id="owner",
            title="Community workshop",
            chat=fixture_chat(),
            created_at=10,
            updated_at=20,
            meta={},
            model_dump=lambda: {
                "id": "chat-a",
                "user_id": "owner",
                "title": "Community workshop",
                "chat": self.chat.chat,
                "created_at": 10,
                "updated_at": 20,
                "share_id": None,
                "archived": False,
                "pinned": False,
                "meta": {},
                "folder_id": None,
            },
        )

    def get_chat_by_id(self, chat_id):
        return self.chat if chat_id == "chat-a" else None

    def get_chat_by_id_and_user_id(self, chat_id, user_id):
        return self.chat if chat_id == "chat-a" and user_id == "owner" else None

    def get_message_by_id_and_message_id(self, chat_id, message_id):
        if chat_id != "chat-a":
            return None
        return self.chat.chat["history"]["messages"].get(message_id)

    def update_chat_by_id(self, chat_id, payload):
        if chat_id != "chat-a":
            return None
        self.chat.chat = copy.deepcopy(payload)
        return self.chat


class ChatMutationStatsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store()

        async def completion(request, form_data, user):
            del request, form_data, user
            return {
                "choices": [
                    {"message": {"content": "Bounded synthetic summary."}}
                ]
            }

        errors = SimpleNamespace(
            ACCESS_PROHIBITED="Access prohibited",
            NOT_FOUND="Not found",
        )
        cls.router = extract(
            ROUTER_PATH,
            class_names=("CompactChatForm", "ResolveToolCallForm"),
            function_names=(
                "_selected_chat_messages",
                "_estimated_context_tokens",
                "_context_usage",
                "_aggregate_chat_stats",
                "_stats_message_projection",
                "_chat_stats_export",
                "_chat_usage_stats",
                "delete_chat_message_by_id",
                "resolve_chat_tool_call",
                "compact_chat_by_id",
            ),
            namespace={
                "Optional": Optional,
                "BaseModel": BaseModel,
                "Field": Field,
                "Counter": Counter,
                "copy": copy,
                "json": json,
                "math": math,
                "HTTPException": HTTPException,
                "status": status,
                "Request": object,
                "Depends": lambda dependency: None,
                "get_verified_user": object(),
                "Chats": cls.store,
                "ERROR_MESSAGES": errors,
                "PrivateChatResponse": object,
                "generate_chat_completion": completion,
                "_saved_chat_response": lambda chat, user: {
                    "id": chat.id,
                    "chat": chat.chat,
                },
            },
        )

    def setUp(self):
        self.store.chat.chat = fixture_chat()
        self.owner = SimpleNamespace(id="owner", role="user")

    def test_statistics_match_selected_and_full_graph_semantics(self):
        stats = self.router["_aggregate_chat_stats"](self.store.chat.chat)
        self.assertEqual(stats["message_count"], 4)
        self.assertEqual(stats["history_message_count"], 6)
        self.assertEqual(stats["models"], {"model-a": 2})
        self.assertEqual(stats["history_models"], {"model-a": 2, "model-b": 1})
        self.assertAlmostEqual(stats["average_response_time"], 11 / 3)
        exported = self.router["_chat_stats_export"](self.store.chat)
        self.assertNotIn("content", exported["chat"]["history"]["messages"]["q-root"])
        self.assertEqual(
            exported["chat"]["history"]["messages"]["q-root"]["content_length"],
            13,
        )

    def test_context_usage_prefers_current_provider_usage_then_estimates(self):
        selected = self.router["_selected_chat_messages"](self.store.chat.chat)
        selected[-1]["usage"] = {"total_tokens": 24}
        self.assertEqual(self.router["_estimated_context_tokens"](selected), 24)
        selected[-1].pop("usage")
        selected[0]["content"] = "Use a synthetic tool"
        selected[1]["content"] = ""
        selected[:] = selected[:2]
        self.assertEqual(self.router["_estimated_context_tokens"](selected), 13)

    def test_delete_removes_turn_pair_and_reparents_remaining_branch(self):
        result = asyncio.run(
            self.router["delete_chat_message_by_id"](
                "chat-a", "q-root", self.owner
            )
        )
        messages = result["chat"]["history"]["messages"]
        self.assertNotIn("q-root", messages)
        self.assertNotIn("a-root", messages)
        self.assertIsNone(messages["q-main"]["parentId"])
        self.assertIsNone(messages["q-alt"]["parentId"])

    def test_persisted_tool_call_fails_closed_without_active_request(self):
        self.store.chat.chat["history"]["messages"]["a-main"]["tool_calls"] = [
            {"id": "call-a"}
        ]
        form = self.router["ResolveToolCallForm"](
            call_id="call-a", action="approve"
        )
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(
                self.router["resolve_chat_tool_call"](
                    "chat-a", "a-main", form, self.owner
                )
            )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "Tool call not found.")

    def test_compaction_summarizes_once_and_then_is_idempotent(self):
        config = SimpleNamespace(
            ENABLE_CONTEXT_COMPACTION=True,
            CONTEXT_COMPACTION_TOKEN_THRESHOLD=1,
            CONTEXT_COMPACTION_RETENTION_PERCENTAGE=40,
            CONTEXT_COMPACTION_PROMPT_TEMPLATE="Summarize synthetic history.",
            CONTEXT_COMPACTION_MODEL="model-a",
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(config=config))
        )
        first = asyncio.run(
            self.router["compact_chat_by_id"](
                request,
                "chat-a",
                self.router["CompactChatForm"](model="model-a"),
                self.owner,
            )
        )
        self.assertTrue(first["compacted"])
        self.assertEqual(first["dropped_messages"], 3)
        self.assertEqual(first["kept_messages"], 1)
        self.assertEqual(
            self.store.chat.chat["history"]["messages"]["a-main"]["contextSummary"],
            "Bounded synthetic summary.",
        )
        second = asyncio.run(
            self.router["compact_chat_by_id"](
                request, "chat-a", None, self.owner
            )
        )
        self.assertFalse(second["compacted"])
        self.assertEqual(second["reason"], "too_short")

    def test_measured_routes_are_declared(self):
        source = ROUTER_PATH.read_text(encoding="utf-8")
        for marker in (
            '@router.delete(\n    "/{id}/messages/{message_id}"',
            '@router.post("/{id}/messages/{message_id}/resolve")',
            '@router.post("/{id}/compact")',
            '@router.get("/stats/export", response_model=ChatStatsExportList)',
            '@router.get("/stats/export/{chat_id}"',
            '@router.get("/stats/usage"',
        ):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
