# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise measured current chat snippet and parameter behavior."""

import ast
import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest

from fastapi import HTTPException, status


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "backend/open_webui/routers/chats.py"


def extract(function_names, namespace):
    selected = []
    for node in ast.parse(ROUTER_PATH.read_text(encoding="utf-8"), filename=str(ROUTER_PATH)).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in function_names:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == set(function_names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTER_PATH), "exec"), namespace)
    return namespace


def fixture():
    prefix = " ".join(f"prefix{i:03d}" for i in range(80))
    suffix = " ".join(f"suffix{i:03d}" for i in range(80))
    messages = [
        {"id": "q", "role": "user", "content": "The AMBER needle phrase."},
        {"id": "a", "role": "assistant", "content": f"{prefix} CENTERMARK {suffix}"},
    ]
    return {
        "title": "Synthetic chat",
        "params": {"seed": 7, "nested": {"original": True}},
        "messages": messages,
        "history": {"messages": {row["id"]: row for row in messages}, "currentId": "a"},
    }


class Store:
    def __init__(self):
        self.chat = SimpleNamespace(id="chat-a", user_id="owner", chat=fixture())

    def get_chat_by_id_and_user_id(self, chat_id, user_id):
        if chat_id == "chat-a" and user_id == "owner":
            return self.chat
        return None

    def update_chat_by_id(self, chat_id, payload):
        if chat_id != "chat-a":
            return None
        self.chat.chat = copy.deepcopy(payload)
        return self.chat


class ChatSnippetParamsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store()
        cls.router = extract(
            {
                "_chat_search_snippet",
                "get_chat_search_snippet",
                "update_chat_params_by_id",
            },
            {
                "Optional": Optional,
                "copy": copy,
                "Chats": cls.store,
                "Depends": lambda dependency: None,
                "get_verified_user": object(),
                "HTTPException": HTTPException,
                "status": status,
                "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="Access prohibited"),
            },
        )

    def setUp(self):
        self.store.chat.chat = fixture()
        self.owner = SimpleNamespace(id="owner", role="user")
        self.foreign = SimpleNamespace(id="foreign", role="user")
        self.admin = SimpleNamespace(id="admin", role="admin")

    def test_search_is_case_insensitive_normalizes_query_and_hides_foreign_chat(self):
        self.assertEqual(
            asyncio.run(self.router["get_chat_search_snippet"]("chat-a", "  amber NEEDLE ", self.owner)),
            "The AMBER needle phrase.",
        )
        self.assertIsNone(
            asyncio.run(self.router["get_chat_search_snippet"]("chat-a", "amber", self.foreign))
        )
        self.assertIsNone(
            asyncio.run(self.router["get_chat_search_snippet"]("chat-a", "amber", self.admin))
        )
        self.assertIsNone(
            asyncio.run(self.router["get_chat_search_snippet"]("missing", "amber", self.owner))
        )

    def test_long_snippet_has_measured_window_and_ascii_ellipses(self):
        snippet = self.router["_chat_search_snippet"](self.store.chat.chat, "centermark")
        self.assertEqual(len(snippet), 206)
        self.assertEqual(snippet.index("CENTERMARK"), 103)
        self.assertTrue(snippet.startswith("...prefix070"))
        self.assertTrue(snippet.endswith("suffix008..."))

    def test_short_matching_message_is_returned_without_truncation(self):
        content = "First paragraph gives ordinary context. Second paragraph remains visible."
        payload = {"messages": [{"role": "assistant", "content": content}]}
        self.assertEqual(self.router["_chat_search_snippet"](payload, "ordinary"), content)

    def test_parameter_updates_merge_shallowly_and_persist(self):
        first = asyncio.run(
            self.router["update_chat_params_by_id"](
                "chat-a", {"temperature": 0.2, "nested": {"first": 1}}, self.owner
            )
        )
        self.assertEqual(first, {"seed": 7, "temperature": 0.2, "nested": {"first": 1}})
        second = asyncio.run(
            self.router["update_chat_params_by_id"](
                "chat-a", {"top_p": 0.8, "nested": {"second": 2}}, self.owner
            )
        )
        self.assertEqual(
            second,
            {"seed": 7, "temperature": 0.2, "top_p": 0.8, "nested": {"second": 2}},
        )
        self.assertEqual(asyncio.run(self.router["update_chat_params_by_id"]("chat-a", {}, self.owner)), second)

    def test_parameter_updates_require_exact_owner_even_for_admin(self):
        for user in (self.foreign, self.admin):
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(
                    self.router["update_chat_params_by_id"](
                        "chat-a", {"forbidden": True}, user
                    )
                )
            self.assertEqual(caught.exception.status_code, 401)
            self.assertEqual(caught.exception.detail, "Access prohibited")


if __name__ == "__main__":
    unittest.main()
