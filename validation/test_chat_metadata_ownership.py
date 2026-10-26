#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted checks for saved-chat completion ownership metadata."""

from __future__ import annotations

import ast
import asyncio
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
MAIN_SOURCE = BASE / "backend/open_webui/main.py"
MIDDLEWARE_SOURCE = BASE / "backend/open_webui/utils/middleware.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Status:
    HTTP_404_NOT_FOUND = 404
    HTTP_400_BAD_REQUEST = 400


class FakeApp:
    def post(self, *_args, **_kwargs):
        return lambda function: function


class CountingState:
    def __init__(self, models):
        self._models = models
        self.model_reads = 0

    @property
    def MODELS(self):
        self.model_reads += 1
        return self._models


class Request:
    def __init__(self, models):
        self.app = type("App", (), {"state": CountingState(models)})()
        self.state = type("RequestState", (), {})()


class Chat:
    def __init__(self, user_id):
        self.user_id = user_id


class ChatsStub:
    def __init__(self, rows):
        self.rows = rows
        self.lookups = []
        self.upserts = []

    def get_chat_by_id(self, chat_id):
        self.lookups.append(chat_id)
        return self.rows.get(chat_id)

    def upsert_message_to_chat_by_id_and_message_id(self, *args):
        self.upserts.append(args)


def extract(path, names, namespace):
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


class ChatMetadataOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.counters = {"model_discovery": 0, "payload": 0, "handler": 0, "response": 0}
        self.chats = ChatsStub({"owned": Chat("member")})
        self.user_dependency = object()

        async def discover_models(_request, user=None):
            self.counters["model_discovery"] += 1

        async def process_payload(request, form_data, user, metadata, model):
            self.counters["payload"] += 1
            return form_data, metadata, []

        async def completion_handler(_request, _form_data, _user):
            self.counters["handler"] += 1
            return "synthetic response"

        async def process_response(*_args):
            self.counters["response"] += 1
            return {"continued": True}

        class ModelsStub:
            @staticmethod
            def get_model_by_id(_model_id):
                return None

        class LogStub:
            @staticmethod
            def debug(*_args):
                pass

        class ProviderErrorResponse:
            pass

        namespace = {
            "app": FakeApp(),
            "Request": Request,
            "Depends": lambda call: call,
            "get_verified_user": self.user_dependency,
            "HTTPException": HTTPError,
            "status": Status,
            "Chats": self.chats,
            "get_all_models": discover_models,
            "Models": ModelsStub,
            "BYPASS_MODEL_ACCESS_CONTROL": True,
            "check_model_access": lambda *_args: None,
            "process_chat_payload": process_payload,
            "chat_completion_handler": completion_handler,
            "process_chat_response": process_response,
            "ProviderErrorResponse": ProviderErrorResponse,
            "log": LogStub(),
        }
        self.route = extract(MAIN_SOURCE, {"chat_completion"}, namespace)["chat_completion"]

    def invoke(self, chat_id, user_id="member", role="user"):
        request = Request({"model": object()})
        user = type("User", (), {"id": user_id, "role": role})()
        body = {"model": "model", "stream": False}
        if chat_id is not OMITTED:
            body["chat_id"] = chat_id
        result = asyncio.run(self.route(request, body, user))
        return result, request

    def test_foreign_and_missing_saved_chats_refuse_before_model_or_payload_effects(self):
        for chat_id, role in (("foreign", "user"), ("missing", "user"), ("foreign", "admin")):
            with self.subTest(chat_id=chat_id, role=role):
                self.counters = {key: 0 for key in self.counters}
                request = Request({"model": object()})
                user = type("User", (), {"id": "member", "role": role})()
                with self.assertRaises(HTTPError) as raised:
                    asyncio.run(
                        self.route(
                            request,
                            {"chat_id": chat_id, "model": "model", "stream": False},
                            user,
                        )
                    )
                self.assertEqual(raised.exception.status_code, 404)
                self.assertEqual(raised.exception.detail, "Chat not found")
                self.assertEqual(request.app.state.model_reads, 0)
                self.assertEqual(self.counters, {key: 0 for key in self.counters})

    def test_owned_saved_chat_continues_and_checks_authenticated_identity(self):
        result, request = self.invoke("owned")
        self.assertEqual(result, {"continued": True})
        self.assertEqual(self.chats.lookups, ["owned"])
        self.assertEqual(self.counters["payload"], 1)
        self.assertEqual(self.counters["handler"], 1)
        self.assertEqual(self.counters["response"], 1)
        self.assertEqual(request.app.state.model_reads, 3)

    def test_absent_none_and_local_contexts_keep_the_existing_path(self):
        self.invoke(OMITTED)
        self.invoke(None)
        self.invoke("local:synthetic-chat")
        self.assertEqual(self.chats.lookups, [])
        self.assertEqual(self.counters["payload"], 3)
        self.assertEqual(self.counters["response"], 3)

    def test_malformed_present_chat_ids_are_refused_before_discovery(self):
        for chat_id in ("", 17, [], {}):
            with self.subTest(chat_id=chat_id):
                request = Request({"model": object()})
                user = type("User", (), {"id": "member", "role": "user"})()
                with self.assertRaises(HTTPError) as raised:
                    asyncio.run(self.route(request, {"chat_id": chat_id}, user))
                self.assertEqual(raised.exception.status_code, 404)
                self.assertEqual(request.app.state.model_reads, 0)

    def test_explicit_false_preserves_title_and_other_values_keep_legacy_fallback(self):
        class TaskNames:
            TITLE_GENERATION = "title_generation"

        title_helper = extract(
            MIDDLEWARE_SOURCE,
            {"_should_generate_default_chat_title"},
            {"TASKS": TaskNames},
        )["_should_generate_default_chat_title"]
        messages = [{"role": "user"}, {"role": "assistant"}]
        self.assertFalse(title_helper({"title_generation": False}, messages))
        for value in (True, None, 0, "legacy"):
            with self.subTest(value=value):
                self.assertTrue(title_helper({"title_generation": value}, messages))
        self.assertFalse(title_helper({"title_generation": True}, messages[:1]))

    def test_response_handler_uses_the_strict_false_title_guard(self):
        tree = ast.parse(MIDDLEWARE_SOURCE.read_text(encoding="utf-8"))
        response_handler = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "process_chat_response"
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_should_generate_default_chat_title"
                for node in ast.walk(response_handler)
            )
        )


class _Omitted:
    pass


OMITTED = _Omitted()


if __name__ == "__main__":
    unittest.main(verbosity=2)
