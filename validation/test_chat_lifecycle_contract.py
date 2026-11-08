# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the measured conversation lifecycle contract."""

import ast
import asyncio
import copy
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Optional
import unittest

from fastapi import HTTPException, status
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "backend/open_webui/routers/chats.py"
MODEL_PATH = ROOT / "backend/open_webui/models/chats.py"


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


class Store:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(
            id="source",
            user_id="owner",
            title="Branching workshop",
            chat={
                "title": "Branching workshop",
                "history": {
                    "currentId": "answer",
                    "messages": {
                        "question": {
                            "id": "question",
                            "parentId": None,
                            "childrenIds": ["answer", "other"],
                            "role": "user",
                            "content": "Question",
                        },
                        "answer": {
                            "id": "answer",
                            "parentId": "question",
                            "childrenIds": [],
                            "role": "assistant",
                            "content": "Answer",
                        },
                        "other": {
                            "id": "other",
                            "parentId": "question",
                            "childrenIds": [],
                            "role": "assistant",
                            "content": "Other",
                        },
                    },
                },
                "messages": [],
            },
        )

    def unarchive_all_chats_by_user_id(self, user_id):
        self.calls.append(("unarchive", user_id))
        return True

    def unshare_all_chats_by_user_id(self, user_id):
        self.calls.append(("unshare", user_id))
        return True

    def mark_chat_unread_by_id_and_user_id(self, chat_id, user_id):
        self.calls.append(("unread", chat_id, user_id))
        if chat_id == "missing":
            return None
        return {
            "chat_id": chat_id,
            "folder_id": None,
            "folder_unread_counts": {},
            "last_read_at": 0,
        }

    def get_chat_by_id_and_user_id(self, chat_id, user_id):
        self.calls.append(("get", chat_id, user_id))
        return self.chat if chat_id == "source" and user_id == "owner" else None

    def insert_forked_chat(self, user_id, source_id, message_id, payload):
        self.calls.append(("fork", user_id, source_id, message_id, payload))
        return SimpleNamespace(model_dump=lambda: {})


class ChatLifecycleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store()
        errors = SimpleNamespace(
            DEFAULT=lambda: "Something went wrong :/", NOT_FOUND="Not found"
        )
        namespace = {
            "Optional": Optional,
            "BaseModel": BaseModel,
            "copy": copy,
            "time": time,
            "HTTPException": HTTPException,
            "status": status,
            "Depends": lambda dependency: None,
            "get_verified_user": object(),
            "Chats": cls.store,
            "ERROR_MESSAGES": errors,
            "PrivateChatResponse": object,
            "_saved_chat_response": lambda chat, user: {"id": "fork"},
        }
        cls.router = extract(
            ROUTER_PATH,
            class_names=("ForkForm",),
            function_names=(
                "unarchive_all_chats",
                "unshare_all_chats",
                "_fork_chat_payload",
                "fork_chat_by_id",
                "mark_chat_unread_by_id",
            ),
            namespace=namespace,
        )

    def setUp(self):
        self.store.calls.clear()
        self.owner = SimpleNamespace(id="owner", role="user")

    def test_bulk_routes_scope_to_verified_user(self):
        self.assertTrue(asyncio.run(self.router["unarchive_all_chats"](self.owner)))
        self.assertTrue(asyncio.run(self.router["unshare_all_chats"](self.owner)))
        self.assertEqual(
            self.store.calls,
            [("unarchive", "owner"), ("unshare", "owner")],
        )

    def test_unread_response_and_missing_contract(self):
        result = asyncio.run(
            self.router["mark_chat_unread_by_id"]("source", self.owner)
        )
        self.assertEqual(result["last_read_at"], 0)
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(self.router["mark_chat_unread_by_id"]("missing", self.owner))
        self.assertEqual(caught.exception.status_code, 404)

    def test_fork_prunes_siblings_and_marks_branch(self):
        payload, selected = self.router["_fork_chat_payload"](self.store.chat, "answer")
        self.assertEqual(selected, "answer")
        self.assertEqual(list(payload["history"]["messages"]), ["question", "answer"])
        self.assertEqual(
            payload["history"]["messages"]["question"]["childrenIds"], ["answer"]
        )
        self.assertEqual(payload["history"]["messages"]["answer"]["childrenIds"], [])
        self.assertTrue(payload["history"]["messages"]["answer"]["done"])
        self.assertEqual(payload["originalChatId"], "source")
        self.assertEqual(payload["branchPointMessageId"], "answer")
        self.assertEqual(payload["title"], "Branching workshop (fork)")

    def test_fork_route_rejects_foreign_and_missing_message(self):
        with self.assertRaises(HTTPException) as foreign:
            asyncio.run(
                self.router["fork_chat_by_id"](
                    "source",
                    self.router["ForkForm"](message_id="answer"),
                    SimpleNamespace(id="foreign", role="user"),
                )
            )
        self.assertEqual(foreign.exception.status_code, 401)
        with self.assertRaises(HTTPException) as missing:
            asyncio.run(
                self.router["fork_chat_by_id"](
                    "source",
                    self.router["ForkForm"](message_id="missing"),
                    self.owner,
                )
            )
        self.assertEqual(missing.exception.status_code, 404)

    def test_measured_paths_and_owner_filtered_mutations_are_present(self):
        router_source = ROUTER_PATH.read_text(encoding="utf-8")
        model_source = MODEL_PATH.read_text(encoding="utf-8")
        for marker in (
            '@router.post("/unarchive/all", response_model=bool)',
            '@router.delete("/share/all", response_model=bool)',
            '@router.post("/{id}/unread")',
            '@router.post("/{id}/fork", response_model=Optional[PrivateChatResponse])',
        ):
            self.assertIn(marker, router_source)
        self.assertIn("filter_by(id=id, user_id=user_id)", model_source)
        self.assertIn(
            "filter(Chat.user_id == user_id, Chat.share_id.is_not(None))",
            model_source,
        )
        self.assertIn('meta.pop("_oa_shared_access_grants", None)', model_source)


if __name__ == "__main__":
    unittest.main()
