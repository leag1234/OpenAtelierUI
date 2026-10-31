#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-isolated Action context checks using the installed Pydantic runtime."""

import ast
import asyncio
import inspect
import json
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_serializer


ROOT = Path(__file__).resolve().parents[1]
CHAT_SOURCE = ROOT / "backend/open_webui/utils/chat.py"
USER_SOURCE = ROOT / "backend/open_webui/models/users.py"


def extract_action_classes():
    tree = ast.parse(USER_SOURCE.read_text())
    wanted = {"UserSettings", "UserModel", "ImportedProfileUserModel"}
    nodes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in wanted]
    namespace = {
        "BaseModel": BaseModel,
        "ConfigDict": ConfigDict,
        "field_serializer": field_serializer,
        "Optional": Optional,
        "date": date,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(USER_SOURCE), "exec"), namespace)
    return namespace["UserModel"], namespace["ImportedProfileUserModel"]


def extract_chat_action(user_model, imported_profile_model):
    tree = ast.parse(CHAT_SOURCE.read_text())
    names = {"chat_action", "_action_user_context"}
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "Any": Any,
        "Request": object,
        "UserModel": user_model,
        "ImportedProfileUserModel": imported_profile_model,
        "inspect": inspect,
        "get_event_emitter": lambda metadata: ("emitter", metadata),
        "get_event_call": lambda metadata: ("event-call", metadata),
        "load_function_module_by_id": lambda *_args: (_ for _ in ()).throw(
            AssertionError("cached Action module should be used")
        ),
        "log": SimpleNamespace(exception=lambda *_args: None),
    }

    class FunctionsStub:
        module = None

        @classmethod
        def get_function_by_id(cls, _identity):
            return object()

        @classmethod
        def get_function_valves_by_id(cls, _identity):
            return {}

        @classmethod
        def get_user_valves_by_id_and_user_id(cls, _identity, _user_id):
            return {"color": "blue"}

    namespace["Functions"] = FunctionsStub
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(CHAT_SOURCE), "exec"), namespace)
    return namespace["chat_action"], namespace["_action_user_context"], FunctionsStub


class ActionProfileContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.UserModel, cls.ImportedProfileUserModel = extract_action_classes()
        chat_action, context_builder, cls.Functions = extract_chat_action(
            cls.UserModel, cls.ImportedProfileUserModel
        )
        cls.chat_action = staticmethod(chat_action)
        cls.context_builder = staticmethod(context_builder)

    def make_user(self, imported=True):
        values = {
            "id": "member-one",
            "name": "Synthetic Member",
            "email": "member@example.invalid",
            "role": "user",
            "profile_image_url": "/user.png",
            "last_active_at": 1,
            "updated_at": 2,
            "created_at": 3,
            "api_key": "synthetic-secret-key",
            "oauth_sub": "synthetic-secret-oauth",
            "info": {"token": "synthetic-secret-info"},
            "settings": {"ui": {"theme": "dark", "density": "compact"}},
        }
        if imported:
            values.update({"bio": "Synthetic profile", "gender": "unspecified", "date_of_birth": date(1990, 4, 5)})
            return self.ImportedProfileUserModel(**values)
        return self.UserModel(**values)

    def make_request(self, module):
        state = SimpleNamespace(
            MODELS={"model-one": {"id": "model-one", "name": "Synthetic model"}},
            FUNCTIONS={"action-one": module},
        )
        return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace())

    def test_opt_in_projection_is_allowlisted_typed_json_and_fresh_per_call(self):
        user = self.make_user(imported=True)
        first = self.context_builder(user)
        self.assertEqual(
            set(first), {"id", "email", "name", "role", "bio", "gender", "date_of_birth", "settings"}
        )
        self.assertEqual(first["date_of_birth"], "1990-04-05")
        self.assertEqual(first["settings"]["ui"]["theme"], "dark")
        self.assertEqual(json.loads(json.dumps(first)), first)
        first["settings"]["ui"]["theme"] = "mutated by Action"
        second = self.context_builder(user)
        self.assertEqual(second["settings"]["ui"]["theme"], "dark")
        self.assertEqual(user.settings.ui["theme"], "dark")
        self.assertNotIn("api_key", first)
        self.assertNotIn("oauth_sub", first)
        self.assertNotIn("info", first)
        self.assertNotIn("token", first)

    def test_default_user_model_keeps_exact_legacy_four_fields(self):
        context = self.context_builder(self.make_user(imported=False))
        self.assertEqual(set(context), {"id", "email", "name", "role"})

    def test_null_profile_values_are_serialized_without_expanding_the_allowlist(self):
        user = self.make_user(imported=True)
        user.bio = None
        user.gender = None
        user.date_of_birth = None
        context = self.context_builder(user)
        self.assertEqual(
            {key: context[key] for key in ("bio", "gender", "date_of_birth")},
            {"bio": None, "gender": None, "date_of_birth": None},
        )
        self.assertIsNone(json.loads(json.dumps(context))["date_of_birth"])

    def test_sync_and_async_action_paths_keep_user_valves_callbacks_and_subaction(self):
        for asynchronous in (False, True):
            seen = []

            class UserValves(BaseModel):
                color: str = "gray"

            if asynchronous:
                async def action(body, __user__, __id__, __model__, __request__, __event_emitter__, __event_call__):
                    seen.append((__user__, __id__, __model__, __request__, __event_emitter__, __event_call__, __user__["settings"]["ui"]["theme"]))
                    __user__["settings"]["ui"]["theme"] = "mutated by Action"
                    return {"chosen": body["choice"]}
            else:
                def action(body, __user__, __id__, __model__, __request__, __event_emitter__, __event_call__):
                    seen.append((__user__, __id__, __model__, __request__, __event_emitter__, __event_call__, __user__["settings"]["ui"]["theme"]))
                    __user__["settings"]["ui"]["theme"] = "mutated by Action"
                    return {"chosen": body["choice"]}

            module = SimpleNamespace(action=action, UserValves=UserValves)
            self.Functions.module = module
            request = self.make_request(module)
            user = self.make_user(imported=True)
            for _ in range(2):
                result = asyncio.run(
                    self.chat_action(
                        request,
                        "action-one.preview",
                        {"model": "model-one", "choice": "Community workshop", "chat_id": "chat-one", "id": "message-one", "session_id": "session-one"},
                        user,
                    )
                )
                self.assertEqual(result, {"chosen": "Community workshop"})
            self.assertEqual([item[6] for item in seen], ["dark", "dark"])
            self.assertEqual(user.settings.ui["theme"], "dark")
            for context, sub_action, model, action_request, emitter, event_call, _theme in seen:
                self.assertEqual(sub_action, "preview")
                self.assertEqual(context["bio"], "Synthetic profile")
                self.assertIsInstance(context["valves"], UserValves)
                self.assertEqual(context["valves"].color, "blue")
                self.assertIs(model, request.app.state.MODELS["model-one"])
                self.assertIs(action_request, request)
                self.assertEqual(emitter[0], "emitter")
                self.assertEqual(event_call[0], "event-call")


if __name__ == "__main__":
    unittest.main(verbosity=2)
