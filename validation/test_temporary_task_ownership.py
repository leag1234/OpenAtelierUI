#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-extracted checks for volatile temporary-task owner binding."""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
TASKS_SOURCE = BASE / "backend/open_webui/tasks.py"
MAIN_SOURCE = BASE / "backend/open_webui/main.py"
MIDDLEWARE_SOURCE = BASE / "backend/open_webui/utils/middleware.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FakeApp:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def post(self, *_args, **_kwargs):
        return lambda function: function


class ChatsStub:
    def __init__(self):
        self.calls = []

    def get_chat_by_id(self, chat_id):
        self.calls.append(chat_id)
        if chat_id == "saved":
            return type("Chat", (), {"user_id": "member"})()
        return None


def task_namespace():
    namespace = {}
    exec(compile(TASKS_SOURCE.read_text(encoding="utf-8"), str(TASKS_SOURCE), "exec"), namespace)
    return namespace


def route_namespace(task_api, chats):
    tree = ast.parse(MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(MAIN_SOURCE))
    names = {"list_tasks_by_chat_id_endpoint", "stop_tasks_by_chat_id_endpoint"}
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "app": FakeApp(),
        "Depends": lambda dependency: dependency,
        "get_verified_user": object(),
        "HTTPException": HTTPError,
        "status": type("Status", (), {"HTTP_404_NOT_FOUND": 404}),
        "Chats": chats,
        "list_task_ids_by_chat_id": task_api["list_task_ids_by_chat_id"],
        "list_task_ids_by_chat_id_for_user": task_api["list_task_ids_by_chat_id_for_user"],
        "get_task": task_api["get_task"],
        "stop_task": task_api["stop_task"],
        "TaskNotFoundError": task_api["TaskNotFoundError"],
    }
    if {node.name for node in nodes} != names:
        raise AssertionError("source is missing temporary task routes")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(MAIN_SOURCE), "exec"), namespace)
    return namespace


class TemporaryTaskOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.task_api = task_namespace()
        self.chats = ChatsStub()
        self.routes = route_namespace(self.task_api, self.chats)

    def test_local_context_is_scoped_to_verified_owner_and_unbound_tasks_stay_hidden(self):
        async def scenario():
            gate = asyncio.Event()

            async def held():
                await gate.wait()

            create = self.task_api["create_task"]
            member_id, _ = create(held(), id="local:shared", owner_user_id="member")
            other_id, _ = create(held(), id="local:shared", owner_user_id="other")
            unbound_id, _ = create(held(), id="local:shared")
            invalid_owner_id, _ = create(held(), id="local:shared", owner_user_id="   ")
            list_route = self.routes["list_tasks_by_chat_id_endpoint"]
            stop_route = self.routes["stop_tasks_by_chat_id_endpoint"]

            self.assertEqual(
                (await list_route("local:shared", user=type("User", (), {"id": "member"})()))["task_ids"],
                [member_id],
            )
            self.assertEqual(
                (await list_route("local:shared", user=type("User", (), {"id": "other"})()))["task_ids"],
                [other_id],
            )
            self.assertEqual(
                (await list_route("local:shared", user=type("User", (), {"id": "unknown"})()))["task_ids"],
                [],
            )
            self.assertEqual(self.task_api["list_task_ids_by_chat_id_for_user"]("local:shared", ""), [])
            self.assertEqual(self.task_api["list_task_ids_by_chat_id_for_user"]("local:shared", "   "), [])
            member_stop = await stop_route("local:shared", user=type("User", (), {"id": "member"})())
            self.assertTrue(member_stop["status"])
            self.assertIsNotNone(self.task_api["get_task"](other_id))
            self.assertIsNotNone(self.task_api["get_task"](unbound_id))
            self.assertIsNotNone(self.task_api["get_task"](invalid_owner_id))
            other_stop = await stop_route("local:shared", user=type("User", (), {"id": "other"})())
            self.assertTrue(other_stop["status"])
            self.assertIsNotNone(self.task_api["get_task"](unbound_id))
            self.assertIsNotNone(self.task_api["get_task"](invalid_owner_id))
            await self.task_api["stop_task"](unbound_id)
            await self.task_api["stop_task"](invalid_owner_id)
            await asyncio.sleep(0)
            self.assertEqual(self.task_api["task_owners"], {})
            self.assertLessEqual(sys.getsizeof(self.task_api["task_owners"]), 4096)
            self.assertEqual(self.task_api["chat_tasks"], {})

        asyncio.run(scenario())

    def test_completed_and_failed_local_tasks_release_volatile_owner(self):
        async def scenario():
            async def succeeds():
                return None

            async def fails():
                raise RuntimeError("synthetic failure")

            good_id, good_task = self.task_api["create_task"](
                succeeds(), id="local:complete", owner_user_id="member"
            )
            await good_task
            await asyncio.sleep(0)
            self.assertNotIn(good_id, self.task_api["task_owners"])

            bad_id, bad_task = self.task_api["create_task"](
                fails(), id="local:failure", owner_user_id="member"
            )
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                await bad_task
            await asyncio.sleep(0)
            self.assertNotIn(bad_id, self.task_api["task_owners"])
            self.assertEqual(self.task_api["chat_tasks"], {})

        asyncio.run(scenario())

    def test_stored_and_bare_local_route_behavior_stays_separate(self):
        async def scenario():
            gate = asyncio.Event()

            async def held():
                await gate.wait()

            task_id, _ = self.task_api["create_task"](held(), id="saved")
            listed = await self.routes["list_tasks_by_chat_id_endpoint"](
                "saved", user=type("User", (), {"id": "member"})()
            )
            self.assertEqual(listed["task_ids"], [task_id])
            self.assertEqual(self.chats.calls, ["saved"])
            self.assertEqual(
                (await self.routes["list_tasks_by_chat_id_endpoint"](
                    "local", user=type("User", (), {"id": "member"})()
                ))["task_ids"],
                [],
            )
            with self.assertRaises(HTTPError) as raised:
                await self.routes["stop_tasks_by_chat_id_endpoint"](
                    "local", user=type("User", (), {"id": "member"})()
                )
            self.assertEqual(raised.exception.status_code, 404)
            await self.task_api["stop_task"](task_id)
            await asyncio.sleep(0)

        asyncio.run(scenario())

    def test_owner_map_releases_capacity_after_large_cleanup_burst(self):
        task_owners = self.task_api["task_owners"]
        cleanup = self.task_api["cleanup_task"]
        task_owners.update({f"task-{index}": "member" for index in range(1000)})
        for index in range(1000):
            cleanup(f"task-{index}")
        self.assertEqual(task_owners, {})
        self.assertLessEqual(sys.getsizeof(task_owners), 4096)

    def test_old_create_task_positional_call_keeps_default_unbound_behavior(self):
        async def scenario():
            async def held():
                await asyncio.Event().wait()

            task_id, _ = self.task_api["create_task"](held(), "local:legacy")
            stored_id, _ = self.task_api["create_task"](held(), id="saved", owner_user_id="member")
            self.assertEqual(self.task_api["task_owners"], {})
            self.assertEqual(self.task_api["list_task_ids_by_chat_id_for_user"]("local:legacy", "member"), [])
            await self.task_api["stop_task"](task_id)
            await self.task_api["stop_task"](stored_id)
            await asyncio.sleep(0)

        asyncio.run(scenario())

    def test_authenticated_route_user_is_the_only_owner_argument(self):
        for path, function_name in ((MAIN_SOURCE, "chat_completion"), (MIDDLEWARE_SOURCE, "process_chat_response")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name)
            calls = [
                node for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "create_task"
            ]
            owner_arguments = [
                keyword.value for call in calls for keyword in call.keywords
                if keyword.arg == "owner_user_id"
            ]
            self.assertEqual(len(owner_arguments), 1)
            self.assertIsInstance(owner_arguments[0], ast.Attribute)
            self.assertEqual(owner_arguments[0].attr, "id")
            self.assertIsInstance(owner_arguments[0].value, ast.Name)
            self.assertEqual(owner_arguments[0].value.id, "user")


if __name__ == "__main__":
    unittest.main(verbosity=2)
