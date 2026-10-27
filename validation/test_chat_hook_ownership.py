#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted ownership checks for completed and Action chat hooks."""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "backend/open_webui/main.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeApp:
    def post(self, *_args, **_kwargs):
        return lambda function: function


class ChatsStub:
    def __init__(self):
        self.lookups = []
        self.rows = {"owned": type("Chat", (), {"user_id": "member"})()}

    def get_chat_by_id(self, chat_id):
        self.lookups.append(chat_id)
        return self.rows.get(chat_id)


class Request:
    def __init__(self):
        self.state = type("State", (), {})()


def extract_hooks(namespace):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    names = {"_validate_private_chat_context", "chat_completed", "chat_action"}
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    if {node.name for node in nodes} != names:
        raise AssertionError("source is missing a required chat hook function")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


class ChatHookOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.chats = ChatsStub()
        self.calls = []

        async def completed(request, form_data, user):
            self.calls.append(("completed", request, dict(form_data), user.id))
            return {"completed": True}

        async def action(request, action_id, form_data, user):
            self.calls.append((action_id, request, dict(form_data), user.id))
            return {"action": action_id}

        self.routes = extract_hooks({
            "app": FakeApp(),
            "Depends": lambda dependency: dependency,
            "get_verified_user": object(),
            "HTTPException": HTTPError,
            "status": type("Status", (), {"HTTP_404_NOT_FOUND": 404, "HTTP_400_BAD_REQUEST": 400}),
            "Chats": self.chats,
            "chat_completed_handler": completed,
            "chat_action_handler": action,
        })

    def call_route(self, route_name, chat_id, role="user", include=True):
        request = Request()
        form = {"model_item": {"direct": True}, "value": "synthetic"}
        if include:
            form["chat_id"] = chat_id
        user = type("User", (), {"id": "member", "role": role})()
        route = self.routes[route_name]
        if route_name == "chat_action":
            result = asyncio.run(route(request, "synthetic-action", form, user))
        else:
            result = asyncio.run(route(request, form, user))
        return result, request, form

    def test_owned_and_local_contexts_keep_existing_dispatch(self):
        for route_name in ("chat_completed", "chat_action"):
            for chat_id, include in (("owned", True), (None, True), (None, False), ("local:temporary", True)):
                with self.subTest(route=route_name, chat_id=chat_id, include=include):
                    self.calls.clear()
                    self.chats.lookups.clear()
                    result, request, _form = self.call_route(route_name, chat_id, include=include)
                    self.assertEqual(len(self.calls), 1)
                    self.assertEqual(request.state.direct, True)
                    self.assertEqual(self.chats.lookups.count("owned"), 1 if chat_id == "owned" else 0)
                    self.assertTrue(result["completed"] if route_name == "chat_completed" else result["action"])
                    self.calls.clear()

    def test_foreign_missing_and_malformed_contexts_refuse_before_mutation_or_dispatch(self):
        for route_name in ("chat_completed", "chat_action"):
            for role, chat_id in (("user", "foreign"), ("user", "missing"), ("admin", "foreign"),
                                  ("user", ""), ("user", 17), ("user", []), ("user", {})):
                with self.subTest(route=route_name, role=role, chat_id=chat_id):
                    request = Request()
                    form = {"chat_id": chat_id, "model_item": {"direct": True}, "value": "synthetic"}
                    user = type("User", (), {"id": "member", "role": role})()
                    before = dict(form)
                    route = self.routes[route_name]
                    with self.assertRaises(HTTPError) as raised:
                        if route_name == "chat_action":
                            asyncio.run(route(request, "synthetic-action", form, user))
                        else:
                            asyncio.run(route(request, form, user))
                    self.assertEqual(raised.exception.status_code, 404)
                    self.assertEqual(raised.exception.detail, "Chat not found")
                    if chat_id:
                        self.assertNotIn(str(chat_id), raised.exception.detail)
                    self.assertEqual(form, before)
                    self.assertFalse(hasattr(request.state, "direct"))
                    self.assertEqual(self.calls, [])
                    expected_lookups = 1 if isinstance(chat_id, str) and chat_id else 0
                    self.assertEqual(len(self.chats.lookups), expected_lookups)
                    self.calls.clear()
                    self.chats.lookups.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
