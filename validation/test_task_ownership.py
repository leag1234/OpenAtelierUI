#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-extracted checks for bounded task ownership and cancellation."""

from __future__ import annotations

import ast
import asyncio
import inspect
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


class Dependency:
    def __init__(self, call):
        self.call = call


class Status:
    HTTP_404_NOT_FOUND = 404


class FakeApp:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def post(self, *_args, **_kwargs):
        return lambda function: function


def extract_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if {node.name for node in nodes} != set(names):
        raise AssertionError(f"missing source functions: {set(names) - {n.name for n in nodes}}")
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace


def task_namespace():
    namespace = {}
    exec(compile(TASKS_SOURCE.read_text(encoding="utf-8"), str(TASKS_SOURCE), "exec"), namespace)
    return namespace


def main_namespace(chats, task_api, verified_user, admin_user):
    namespace = {
        "app": FakeApp(),
        "Depends": lambda call: Dependency(call),
        "HTTPException": HTTPError,
        "status": Status,
        "Chats": chats,
        "list_tasks": task_api["list_tasks"],
        "list_task_ids_by_chat_id": task_api["list_task_ids_by_chat_id"],
        "stop_task": task_api["stop_task"],
        "get_task": task_api["get_task"],
        "TaskNotFoundError": task_api["TaskNotFoundError"],
        "get_verified_user": verified_user,
        "get_admin_user": admin_user,
    }
    return extract_functions(
        MAIN_SOURCE,
        {
            "stop_task_endpoint",
            "list_tasks_endpoint",
            "list_tasks_by_chat_id_endpoint",
            "stop_tasks_by_chat_id_endpoint",
        },
        namespace,
    )


class FakeChats:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_chat_by_id(self, chat_id):
        self.calls.append(chat_id)
        return self.rows.get(chat_id)


class Chat:
    def __init__(self, user_id):
        self.user_id = user_id


class TaskOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.task_api = task_namespace()
        self.admin = object()
        self.verified = object()
        self.chats = FakeChats({"owned": Chat("member"), "foreign": Chat("other")})
        self.routes = main_namespace(self.chats, self.task_api, self.verified, self.admin)

    def test_global_task_routes_remain_admin_only(self):
        for route_name in ("list_tasks_endpoint", "stop_task_endpoint"):
            route = self.routes[route_name]
            self.assertIs(inspect.signature(route).parameters["user"].default.call, self.admin)
        route = self.routes["list_tasks_by_chat_id_endpoint"]
        self.assertIs(inspect.signature(route).parameters["user"].default.call, self.verified)
        route = self.routes["stop_tasks_by_chat_id_endpoint"]
        self.assertIs(inspect.signature(route).parameters["user"].default.call, self.verified)

    def test_global_stop_maps_only_registry_absence_to_not_found(self):
        async def missing(_task_id):
            raise self.task_api["TaskNotFoundError"]("missing task")

        self.routes["stop_task"] = missing
        with self.assertRaises(HTTPError) as raised:
            asyncio.run(self.routes["stop_task_endpoint"]("id", user=object()))
        self.assertEqual(raised.exception.status_code, 404)

        async def task_failure(_task_id):
            raise ValueError("synthetic task failure")

        self.routes["stop_task"] = task_failure
        with self.assertRaisesRegex(ValueError, "synthetic task failure"):
            asyncio.run(self.routes["stop_task_endpoint"]("id", user=object()))

    def test_foreign_and_missing_chat_refuse_before_task_registry_access(self):
        async def forbidden(*_args):
            raise AssertionError("task registry accessed before ownership check")

        self.routes["list_task_ids_by_chat_id"] = forbidden
        self.routes["get_task"] = forbidden
        self.routes["stop_task"] = forbidden
        for chat_id in ("foreign", "missing"):
            with self.subTest(chat_id=chat_id):
                with self.assertRaises(HTTPError) as raised:
                    asyncio.run(
                        self.routes["stop_tasks_by_chat_id_endpoint"](
                            chat_id, user=type("User", (), {"id": "member"})()
                        )
                    )
                self.assertEqual(raised.exception.status_code, 404)

    def test_owned_empty_chat_has_truthful_no_stop_response(self):
        empty = asyncio.run(
            self.routes["stop_tasks_by_chat_id_endpoint"](
                "owned", user=type("User", (), {"id": "member"})()
            )
        )
        self.assertEqual(empty, {"status": False, "message": "No active tasks were stopped."})
        self.assertEqual(self.chats.calls, ["owned"])

    def test_known_finished_race_is_skipped_but_other_errors_propagate(self):
        stopped = []
        self.routes["list_task_ids_by_chat_id"] = lambda _chat: ["finished", "active"]

        def get_task(task_id):
            return None if task_id == "finished" else object()

        async def stop_task(task_id):
            stopped.append(task_id)
            return {"status": True, "message": "stopped"}

        self.routes["get_task"] = get_task
        self.routes["stop_task"] = stop_task
        result = asyncio.run(
            self.routes["stop_tasks_by_chat_id_endpoint"](
                "owned", user=type("User", (), {"id": "member"})()
            )
        )
        self.assertEqual(stopped, ["active"])
        self.assertEqual(result["status"], True)

        lookup_count = {"racing": 0}

        def racing_lookup(task_id):
            if task_id == "racing":
                lookup_count["racing"] += 1
                return object() if lookup_count["racing"] == 1 else None
            return object()

        async def raced_stop(_task_id):
            raise self.task_api["TaskNotFoundError"]("task finished during stop")

        self.routes["list_task_ids_by_chat_id"] = lambda _chat: ["racing"]
        self.routes["get_task"] = racing_lookup
        self.routes["stop_task"] = raced_stop
        result = asyncio.run(
            self.routes["stop_tasks_by_chat_id_endpoint"](
                "owned", user=type("User", (), {"id": "member"})()
            )
        )
        self.assertFalse(result["status"])
        self.assertEqual(lookup_count["racing"], 2)

        async def arbitrary_failure(_task_id):
            raise RuntimeError("synthetic task failure")

        self.routes["get_task"] = lambda _task_id: object()
        self.routes["stop_task"] = arbitrary_failure
        with self.assertRaisesRegex(RuntimeError, "synthetic task failure"):
            asyncio.run(
                self.routes["stop_tasks_by_chat_id_endpoint"](
                    "owned", user=type("User", (), {"id": "member"})()
                )
            )

        async def value_error_from_task(_task_id):
            raise ValueError("synthetic task failure")

        self.routes["list_task_ids_by_chat_id"] = lambda _chat: ["value-error"]
        self.routes["get_task"] = lambda _task_id: object()
        self.routes["stop_task"] = value_error_from_task
        with self.assertRaisesRegex(ValueError, "synthetic task failure"):
            asyncio.run(
                self.routes["stop_tasks_by_chat_id_endpoint"](
                    "owned", user=type("User", (), {"id": "member"})()
                )
            )

    def test_cancel_request_is_reported_even_if_coroutine_handles_cancel(self):
        async def handles_cancel():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return "handled"

        async def exercise():
            task_id, task = self.task_api["create_task"](handles_cancel(), id="owned")
            await asyncio.sleep(0)
            result = await self.task_api["stop_task"](task_id)
            await asyncio.sleep(0)
            self.assertEqual(result["status"], True)
            self.assertEqual(task.result(), "handled")
            self.assertNotIn(task_id, self.task_api["tasks"])
            self.assertNotIn(task_id, self.task_api["list_task_ids_by_chat_id"]("owned"))

        asyncio.run(exercise())

        async def propagated_exercise():
            async def propagates_cancel():
                await asyncio.Event().wait()

            task_id, _task = self.task_api["create_task"](propagates_cancel(), id="owned")
            await asyncio.sleep(0)
            result = await self.task_api["stop_task"](task_id)
            self.assertTrue(result["status"])

        asyncio.run(propagated_exercise())

    def test_completed_task_is_not_reported_as_cancelled(self):
        async def exercise():
            async def completed():
                return "done"

            task = asyncio.create_task(completed())
            await task
            self.task_api["tasks"]["already-done"] = task
            result = await self.task_api["stop_task"]("already-done")
            self.assertFalse(result["status"])

        asyncio.run(exercise())

    def test_task_failure_is_not_swallowed_by_stop(self):
        async def exercise():
            async def failed():
                raise ValueError("synthetic task failure")

            task = asyncio.create_task(failed())
            try:
                await task
            except ValueError:
                pass
            self.task_api["tasks"]["failed"] = task
            with self.assertRaisesRegex(ValueError, "synthetic task failure"):
                await self.task_api["stop_task"]("failed")

        asyncio.run(exercise())

    def test_missing_and_null_features_are_empty_but_malformed_values_stay_errors(self):
        namespace = extract_functions(MIDDLEWARE_SOURCE, {"_metadata_feature_enabled"}, {})
        enabled = namespace["_metadata_feature_enabled"]
        self.assertFalse(enabled({}, "code_interpreter"))
        self.assertFalse(enabled({"features": None}, "code_interpreter"))
        self.assertTrue(enabled({"features": {"code_interpreter": True}}, "code_interpreter"))
        for malformed in (False, [], "unexpected"):
            with self.subTest(features=malformed):
                with self.assertRaises(AttributeError):
                    enabled({"features": malformed}, "code_interpreter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
