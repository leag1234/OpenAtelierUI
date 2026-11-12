#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for owner-bound temporary-chat reconnect admission."""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
TASKS_SOURCE = BASE / "backend/open_webui/tasks.py"
MAIN_SOURCE = BASE / "backend/open_webui/main.py"
SOCKET_SOURCE = BASE / "backend/open_webui/socket/main.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeApp:
    def post(self, *_args, **_kwargs):
        return lambda function: function


def task_namespace():
    namespace = {}
    exec(compile(TASKS_SOURCE.read_text(encoding="utf-8"), str(TASKS_SOURCE), "exec"), namespace)
    return namespace


def route_namespace(task_api, session_owner):
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "resume_temporary_chat"
    )
    namespace = {
        "app": FakeApp(),
        "Any": object,
        "JsonObject": dict,
        "Depends": lambda dependency: dependency,
        "get_verified_user": object(),
        "HTTPException": HTTPError,
        "status": type("Status", (), {"HTTP_404_NOT_FOUND": 404}),
        "list_task_ids_by_chat_id_for_user": task_api["list_task_ids_by_chat_id_for_user"],
        "get_task": task_api["get_task"],
        "is_ephemeral_chat_id": task_api["is_ephemeral_chat_id"],
        "socket_session_belongs_to_user": session_owner,
    }
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(MAIN_SOURCE), "exec"), namespace)
    return namespace["resume_temporary_chat"]


def socket_user_function():
    tree = ast.parse(SOCKET_SOURCE.read_text(encoding="utf-8"), filename=str(SOCKET_SOURCE))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_socket_user"
    )

    class RequestStub:
        def __init__(self, scope):
            if scope.get("type") != "http":
                raise AssertionError(scope)
            self.scope = scope

    namespace = {
        "Request": RequestStub,
        "HTTPAuthorizationCredentials": lambda scheme, credentials: {
            "scheme": scheme,
            "credentials": credentials,
        },
        "get_current_user": lambda request, _api_key, credentials: {
            "request": request,
            "credentials": credentials,
        },
        "get_verified_user": lambda value: value,
        "HTTPException": HTTPError,
    }
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOCKET_SOURCE), "exec"), namespace)
    return namespace["_socket_user"]


class TemporaryChatResumeTests(unittest.TestCase):
    def setUp(self):
        self.tasks = task_namespace()
        self.sessions = {"session-member": "member", "session-other": "other"}
        self.route = route_namespace(
            self.tasks,
            lambda session_id, user_id: self.sessions.get(session_id) == user_id,
        )

    def test_active_temporary_chat_resumes_only_for_its_owner_session(self):
        async def scenario():
            gate = asyncio.Event()

            async def held():
                await gate.wait()

            task_id, _ = self.tasks["create_task"](
                held(), id="temporary:old-session:chat", owner_user_id="member"
            )
            user = type("User", (), {"id": "member"})()
            result = await self.route(
                "temporary:old-session:chat",
                {"session_id": "session-member"},
                user=user,
            )
            self.assertEqual(result, {"status": True, "task_ids": [task_id]})

            for chat_id, form, actor in (
                ("temporary:old-session:chat", {"session_id": "session-other"}, user),
                ("temporary:old-session:chat", {}, user),
                ("saved-chat", {"session_id": "session-member"}, user),
                (
                    "temporary:old-session:chat",
                    {"session_id": "session-other"},
                    type("User", (), {"id": "other"})(),
                ),
            ):
                with self.assertRaises(HTTPError) as raised:
                    await self.route(chat_id, form, user=actor)
                self.assertEqual(raised.exception.status_code, 404)
                self.assertEqual(raised.exception.detail, "Temporary chat expired or unavailable")

            await self.tasks["stop_task"](task_id)
            await asyncio.sleep(0)
            with self.assertRaises(HTTPError):
                await self.route(
                    "temporary:old-session:chat",
                    {"session_id": "session-member"},
                    user=user,
                )

        asyncio.run(scenario())

    def test_legacy_local_context_remains_supported(self):
        self.assertTrue(self.tasks["is_ephemeral_chat_id"]("local:legacy"))
        self.assertTrue(self.tasks["is_ephemeral_chat_id"]("temporary:session:modern"))
        self.assertFalse(self.tasks["is_ephemeral_chat_id"]("saved-chat"))

    def test_socket_auth_reuses_real_asgi_app_scope_as_http_request(self):
        authenticate = socket_user_function()
        app = object()
        result = authenticate(
            "synthetic-token",
            {"type": "websocket", "app": app, "state": {"marker": "kept"}},
        )
        self.assertIs(result["request"].scope["app"], app)
        self.assertEqual(result["request"].scope["type"], "http")
        self.assertEqual(result["request"].scope["state"], {"marker": "kept"})
        self.assertEqual(result["credentials"]["credentials"], "synthetic-token")
        relay_result = authenticate("synthetic-token", {"type": "websocket"})
        self.assertNotIn("app", relay_result["request"].scope)
        self.assertEqual(relay_result["request"].scope["path"], "/ws/socket.io")
        self.assertEqual(relay_result["credentials"]["credentials"], "synthetic-token")
        self.assertIsNone(authenticate("sk-synthetic", {"type": "websocket", "app": app}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
