#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted checks for opt-in asynchronous plugin completions."""

from __future__ import annotations

import ast
import asyncio
import os
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
MAIN_SOURCE = BASE / "backend/open_webui/main.py"
ASYNC_PLUGIN_SOURCE = BASE / "backend/open_webui/utils/async_plugin.py"
TASKS_SOURCE = BASE / "backend/open_webui/tasks.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Status:
    HTTP_400_BAD_REQUEST = 400
    HTTP_404_NOT_FOUND = 404


class FakeApp:
    def post(self, *_args, **_kwargs):
        return lambda function: function


class FakeRequest:
    def __init__(self, models):
        self.app = type("App", (), {"state": type("State", (), {"MODELS": models})()})()
        self.state = type("RequestState", (), {})()


class Chat:
    user_id = "member"


class ChatsStub:
    def __init__(self):
        self.rows = {"saved-chat": Chat()}
        self.lookups = []
        self.upserts = []

    def get_chat_by_id(self, chat_id):
        self.lookups.append(chat_id)
        return self.rows.get(chat_id)

    def upsert_message_to_chat_by_id_and_message_id(self, *args):
        self.upserts.append(args)


class User:
    id = "member"
    role = "user"
    email = "member@example.invalid"
    name = "Synthetic member"


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


def load_tasks():
    namespace = {}
    exec(compile(TASKS_SOURCE.read_text(encoding="utf-8"), str(TASKS_SOURCE), "exec"), namespace)
    return namespace


def load_async_plugin(chats, emissions, response_calls):
    async def emitter(event):
        emissions.append(event)

    async def process_response(*args):
        response_calls.append(args)

    namespace = {
        "asyncio": asyncio,
        "Chats": chats,
        "get_event_emitter": lambda _metadata: emitter,
        "process_chat_response": process_response,
    }
    return extract_functions(
        ASYNC_PLUGIN_SOURCE,
        {
            "is_supported_async_plugin_response",
            "_emit_async_plugin_failure",
            "run_async_plugin_completion",
        },
        namespace,
    )


def load_main(chats, tasks_api, run_async):
    counters = {"discovery": 0, "payload": 0, "generation": 0, "processing": 0}
    model = {"pipe": {"type": "pipe"}}

    async def get_all_models(_request, user=None):
        counters["discovery"] += 1

    class ModelsStub:
        @staticmethod
        def get_model_by_id(_model_id):
            return None

    async def process_payload(request, form_data, user, metadata, selected_model):
        counters["payload"] += 1
        return form_data, metadata, [{"type": "synthetic-progress"}]

    async def generation(_request, _form_data, _user):
        counters["generation"] += 1
        return {"choices": [{"message": {"content": "synthetic useful answer"}}]}

    async def process_response(*_args):
        counters["processing"] += 1
        return {"processed": True}

    middleware_namespace = {
        "asyncio": asyncio,
        "Chats": chats,
        "get_event_emitter": lambda _metadata: (lambda _event: asyncio.sleep(0)),
        "process_chat_response": process_response,
    }
    middleware = extract_functions(
        ASYNC_PLUGIN_SOURCE,
        {
            "is_supported_async_plugin_response",
            "_emit_async_plugin_failure",
            "run_async_plugin_completion",
        },
        middleware_namespace,
    )
    run_async = middleware["run_async_plugin_completion"]

    namespace = {
        "os": os,
        "app": FakeApp(),
        "Request": FakeRequest,
        "Depends": lambda dependency: dependency,
        "get_verified_user": object(),
        "HTTPException": HTTPError,
        "status": Status,
        "Chats": chats,
        "get_all_models": get_all_models,
        "Models": ModelsStub,
        "BYPASS_MODEL_ACCESS_CONTROL": True,
        "check_model_access": lambda *_args: None,
        "process_chat_payload": process_payload,
        "chat_completion_handler": generation,
        "process_chat_response": process_response,
        "run_async_plugin_completion": run_async,
        "create_task": tasks_api["create_task"],
        "ProviderErrorResponse": type("ProviderErrorResponse", (), {}),
        "log": type("Log", (), {"debug": staticmethod(lambda *_args: None)})(),
    }
    route = extract_functions(
        MAIN_SOURCE,
        {"_async_plugin_compat_requested", "chat_completion"},
        namespace,
    )
    return route, counters, model


class AsyncPluginCompatTests(unittest.TestCase):
    def setUp(self):
        self.prior_flag = os.environ.pop("OPENATELIER_ASYNC_PLUGIN_COMPAT", None)
        self.tasks = load_tasks()
        self.chats = ChatsStub()

    def tearDown(self):
        os.environ.pop("OPENATELIER_ASYNC_PLUGIN_COMPAT", None)
        if self.prior_flag is not None:
            os.environ["OPENATELIER_ASYNC_PLUGIN_COMPAT"] = self.prior_flag

    def test_gate_is_opt_in_and_accepts_only_explicit_supported_metadata(self):
        namespace = extract_functions(
            MAIN_SOURCE, {"_async_plugin_compat_requested"}, {"os": os}
        )
        select = namespace["_async_plugin_compat_requested"]
        form = {"stream": False}
        model_item = {}
        model = {"pipe": {"type": "pipe"}}
        metadata = {"chat_id": "saved-chat", "message_id": "message", "session_id": "session"}
        self.assertFalse(select(form, model_item, model, metadata))
        os.environ["OPENATELIER_ASYNC_PLUGIN_COMPAT"] = "1"
        self.assertTrue(select(form, model_item, model, metadata))
        for bad_form, bad_item, bad_model, bad_metadata in (
            ({}, model_item, model, metadata),
            ({"stream": True}, model_item, model, metadata),
            ({"stream": 0}, model_item, model, metadata),
            (form, {"direct": True}, model, metadata),
            (form, model_item, {}, metadata),
            (form, model_item, model, {**metadata, "session_id": ""}),
            (form, model_item, model, {**metadata, "message_id": 2}),
        ):
            with self.subTest(candidate=bad_form):
                self.assertFalse(select(bad_form, bad_item, bad_model, bad_metadata))

    def test_route_returns_ack_before_generation_then_processes_once(self):
        async def exercise():
            generation_started = asyncio.Event()
            release_generation = asyncio.Event()
            counters = {"discovery": 0, "payload": 0, "generation": 0, "processing": 0}
            model = {"pipe": {"type": "pipe"}}

            async def discover(_request, user=None):
                counters["discovery"] += 1

            class ModelsStub:
                @staticmethod
                def get_model_by_id(_model_id):
                    return None

            async def payload(request, form_data, user, metadata, selected_model):
                counters["payload"] += 1
                return form_data, metadata, [{"type": "synthetic-progress"}]

            async def generate(_request, _form_data, _user):
                counters["generation"] += 1
                generation_started.set()
                await release_generation.wait()
                return {"choices": [{"message": {"content": "synthetic useful answer"}}]}

            async def process_response(*_args):
                counters["processing"] += 1

            async def emitter(_event):
                return None

            middleware = extract_functions(
                ASYNC_PLUGIN_SOURCE,
                {
                    "is_supported_async_plugin_response",
                    "_emit_async_plugin_failure",
                    "run_async_plugin_completion",
                },
                {
                    "asyncio": asyncio,
                    "Chats": self.chats,
                    "get_event_emitter": lambda _metadata: emitter,
                    "process_chat_response": process_response,
                },
            )
            main = extract_functions(
                MAIN_SOURCE,
                {"_async_plugin_compat_requested", "chat_completion"},
                {
                    "os": os,
                    "app": FakeApp(),
                    "Request": FakeRequest,
                    "Depends": lambda dependency: dependency,
                    "get_verified_user": object(),
                    "HTTPException": HTTPError,
                    "status": Status,
                    "Chats": self.chats,
                    "get_all_models": discover,
                    "Models": ModelsStub,
                    "BYPASS_MODEL_ACCESS_CONTROL": True,
                    "check_model_access": lambda *_args: None,
                    "process_chat_payload": payload,
                    "chat_completion_handler": generate,
                    "process_chat_response": process_response,
                    "run_async_plugin_completion": middleware["run_async_plugin_completion"],
                    "create_task": self.tasks["create_task"],
                    "ProviderErrorResponse": type("ProviderErrorResponse", (), {}),
                    "log": type("Log", (), {"debug": staticmethod(lambda *_args: None)})(),
                },
            )
            request = FakeRequest({"synthetic-pipe": model})
            form = {
                "model": "synthetic-pipe",
                "stream": False,
                "chat_id": "saved-chat",
                "id": "message",
                "session_id": "session",
            }
            os.environ["OPENATELIER_ASYNC_PLUGIN_COMPAT"] = "1"
            ack = await main["chat_completion"](request, form, User())
            self.assertEqual(ack["status"], True)
            self.assertEqual(len(ack["task_ids"]), 1)
            self.assertEqual(ack["chat_id"], "saved-chat")
            self.assertFalse(generation_started.is_set())
            self.assertEqual(counters["generation"], 0)
            self.assertEqual(counters["processing"], 0)
            task = self.tasks["get_task"](ack["task_ids"][0])
            self.assertIsNotNone(task)
            await generation_started.wait()
            release_generation.set()
            await task
            await asyncio.sleep(0)
            self.assertEqual(counters["generation"], 1)
            self.assertEqual(counters["processing"], 1)
            self.assertIsNone(self.tasks["get_task"](ack["task_ids"][0]))

        asyncio.run(exercise())

    def test_default_off_keeps_immediate_response_path(self):
        async def exercise():
            route, counters, model = load_main(self.chats, self.tasks, None)
            del model
            request = FakeRequest({"synthetic-pipe": {"pipe": {"type": "pipe"}}})
            body = {
                "model": "synthetic-pipe",
                "stream": False,
                "chat_id": "saved-chat",
                "id": "message",
                "session_id": "session",
            }
            result = await route["chat_completion"](request, body, User())
            self.assertEqual(result, {"processed": True})
            self.assertEqual(counters["generation"], 1)
            self.assertEqual(counters["processing"], 1)
            self.assertEqual(self.tasks["list_task_ids_by_chat_id"]("saved-chat"), [])

        asyncio.run(exercise())

    def test_only_normal_nonempty_completion_dictionaries_are_supported(self):
        namespace = load_async_plugin(self.chats, [], [])
        supported = namespace["is_supported_async_plugin_response"]
        self.assertTrue(supported({"choices": [{"message": {"content": "answer"}}]}))
        for response in (
            None,
            StreamingShape(),
            {},
            {"choices": []},
            {"choices": [{"message": {"content": ""}}]},
            {"error": {"detail": "sensitive"}},
        ):
            with self.subTest(response_type=type(response).__name__):
                self.assertFalse(supported(response))

    def test_failure_and_unsupported_response_emit_fixed_sanitized_error(self):
        async def exercise():
            emissions = []
            processed = []
            middleware = load_async_plugin(self.chats, emissions, processed)
            secret = "SYNTHETIC_SECRET_DO_NOT_ECHO"

            async def failed(*_args):
                raise RuntimeError(secret)

            metadata = {"chat_id": "saved-chat", "message_id": "message"}
            with self.assertRaisesRegex(RuntimeError, "SYNTHETIC_SECRET_DO_NOT_ECHO"):
                await middleware["run_async_plugin_completion"](
                    object(), failed, {}, User(), metadata, {}, [], None
                )
            self.assertEqual(len(emissions), 1)
            self.assertEqual(
                emissions[0]["data"],
                {"error": {"content": "The plugin request could not be completed."}, "done": True},
            )
            self.assertNotIn(secret, repr(emissions))
            self.assertNotIn(secret, repr(self.chats.upserts))
            self.assertEqual(processed, [])

            emissions.clear()

            async def nonstandard(*_args):
                return StreamingShape()

            await middleware["run_async_plugin_completion"](
                object(), nonstandard, {}, User(), metadata, {}, [], None
            )
            self.assertEqual(
                emissions[0]["data"],
                {
                    "error": {
                        "content": "The plugin returned a response that could not be processed."
                    },
                    "done": True,
                },
            )
            self.assertEqual(processed, [])

        asyncio.run(exercise())

    def test_cancellation_emits_existing_event_and_task_cleanup_retrieves_errors(self):
        async def exercise():
            emissions = []
            middleware = load_async_plugin(self.chats, emissions, [])
            started = asyncio.Event()

            async def held(*_args):
                started.set()
                await asyncio.Event().wait()

            metadata = {"chat_id": "saved-chat", "message_id": "message"}
            task_id, task = self.tasks["create_task"](
                middleware["run_async_plugin_completion"](
                    object(), held, {}, User(), metadata, {}, [], None
                ),
                id="saved-chat",
            )
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(emissions, [{"type": "task-cancelled"}])
            self.assertIsNone(self.tasks["get_task"](task_id))
            self.assertEqual(self.tasks["list_task_ids_by_chat_id"]("saved-chat"), [])

            # Cancellation at the runner's initial yield also produces the owned event.
            emissions.clear()
            initial_yield_task = asyncio.create_task(
                middleware["run_async_plugin_completion"](
                    object(), held, {}, User(), metadata, {}, [], None
                )
            )
            await asyncio.sleep(0)
            initial_yield_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await initial_yield_task
            self.assertEqual(emissions, [{"type": "task-cancelled"}])

            async def unexpected_failure():
                raise RuntimeError("synthetic unhandled failure")

            failed_id, failed_task = self.tasks["create_task"](unexpected_failure())
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertIsNone(self.tasks["get_task"](failed_id))
            self.assertFalse(failed_task._log_traceback)

            async def plugin_failure(*_args):
                raise RuntimeError("SYNTHETIC_PLUGIN_FAILURE_MARKER")

            failing_id, failing_task = self.tasks["create_task"](
                middleware["run_async_plugin_completion"](
                    object(), plugin_failure, {}, User(), metadata, {}, [], None
                ),
                id="saved-chat",
            )
            with self.assertRaisesRegex(RuntimeError, "SYNTHETIC_PLUGIN_FAILURE_MARKER"):
                await failing_task
            await asyncio.sleep(0)
            self.assertIsNone(self.tasks["get_task"](failing_id))
            self.assertEqual(self.tasks["list_task_ids_by_chat_id"]("saved-chat"), [])
            self.assertNotIn("SYNTHETIC_PLUGIN_FAILURE_MARKER", repr(emissions))
            self.assertNotIn("SYNTHETIC_PLUGIN_FAILURE_MARKER", repr(self.chats.upserts))

        asyncio.run(exercise())


class StreamingShape:
    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
