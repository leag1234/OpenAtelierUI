# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the measured chat navigation and configuration contract."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
import unittest

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "backend/open_webui/routers/chats.py"
MODEL_PATH = ROOT / "backend/open_webui/models/chats.py"
CONFIG_PATH = ROOT / "backend/open_webui/config.py"
MAIN_PATH = ROOT / "backend/open_webui/main.py"


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


class ChatStore:
    def __init__(self):
        self.calls = []

    def count_archived_chats_by_user_id(self, user_id):
        self.calls.append(("count", user_id))
        return 2

    def get_chat_title_id_list_by_folder_id_and_user_id(self, *args, **kwargs):
        self.calls.append(("folder", args, kwargs))
        return [{"id": "chat-a"}]

    def get_shared_chat_list_by_user_id(self, *args, **kwargs):
        self.calls.append(("shared", args, kwargs))
        return [{"id": "chat-shared"}]


class ChatNavigationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = ChatStore()
        namespace = {
            "Optional": Optional,
            "BaseModel": BaseModel,
            "Request": object,
            "Depends": lambda dependency: None,
            "get_verified_user": object(),
            "get_admin_user": object(),
            "Chats": cls.store,
        }
        cls.router = extract(
            ROUTER_PATH,
            class_names=("ChatConfigForm",),
            function_names=(
                "get_archived_session_user_chat_count",
                "get_chat_list_by_folder_id",
                "get_shared_session_user_chat_list",
                "_chat_config_values",
                "get_chat_config",
                "set_chat_config",
            ),
            namespace=namespace,
        )
        cls.models = extract(
            MODEL_PATH,
            class_names=(
                "ChatTitleIdResponse",
                "PrivateChatTitleIdResponse",
                "SharedChatResponse",
            ),
            namespace={"Optional": Optional, "BaseModel": BaseModel},
        )

    def setUp(self):
        self.store.calls.clear()
        self.owner = SimpleNamespace(id="owner", role="user")

    def test_response_models_match_measured_public_fields(self):
        title_fields = self.models["ChatTitleIdResponse"].model_fields
        self.assertEqual(
            set(title_fields),
            {
                "id",
                "title",
                "updated_at",
                "created_at",
                "last_read_at",
                "snippet",
                "active",
            },
        )
        self.assertFalse(title_fields["active"].default)
        self.assertEqual(
            set(self.models["SharedChatResponse"].model_fields),
            {"id", "chat_id", "title", "updated_at", "created_at", "share_id"},
        )
        self.assertIn(
            "archived", self.models["PrivateChatTitleIdResponse"].model_fields
        )

    def test_count_folder_and_shared_routes_scope_to_verified_user(self):
        count = asyncio.run(
            self.router["get_archived_session_user_chat_count"](user=self.owner)
        )
        folder = asyncio.run(
            self.router["get_chat_list_by_folder_id"](
                "folder-a", 2, "title", "asc", user=self.owner
            )
        )
        shared = asyncio.run(
            self.router["get_shared_session_user_chat_list"](
                1, "Shared", "updated_at", "asc", user=self.owner
            )
        )
        self.assertEqual(count, 2)
        self.assertEqual(folder, [{"id": "chat-a"}])
        self.assertEqual(shared, [{"id": "chat-shared"}])
        self.assertEqual(
            self.store.calls,
            [
                ("count", "owner"),
                (
                    "folder",
                    ("folder-a", "owner"),
                    {"page": 2, "sort_by": "title", "sort_dir": "asc"},
                ),
                (
                    "shared",
                    ("owner",),
                    {
                        "page": 1,
                        "query_text": "Shared",
                        "order_by": "updated_at",
                        "direction": "asc",
                    },
                ),
            ],
        )

    def test_configuration_update_is_persistent_shaped_and_clamped(self):
        config = SimpleNamespace(
            ENABLE_CONTEXT_COMPACTION=False,
            CONTEXT_COMPACTION_TOKEN_THRESHOLD=80000,
            CONTEXT_COMPACTION_PROMPT_TEMPLATE="",
            CONTEXT_COMPACTION_MODEL="",
            CONTEXT_COMPACTION_RETENTION_PERCENTAGE=40,
            CONTEXT_COMPACTION_TOKEN_CAP=80000,
            ENABLE_TOOL_PERMISSIONS=False,
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(config=config))
        )
        form = self.router["ChatConfigForm"](
            ENABLE_CONTEXT_COMPACTION=True,
            CONTEXT_COMPACTION_TOKEN_THRESHOLD=12345,
            CONTEXT_COMPACTION_PROMPT_TEMPLATE="Synthetic compaction prompt",
            CONTEXT_COMPACTION_MODEL="synthetic-model",
            CONTEXT_COMPACTION_RETENTION_PERCENTAGE=55,
            CONTEXT_COMPACTION_TOKEN_CAP=23456,
            ENABLE_TOOL_PERMISSIONS=True,
        )
        result = asyncio.run(
            self.router["set_chat_config"](request, form, user=SimpleNamespace())
        )
        self.assertEqual(result.CONTEXT_COMPACTION_RETENTION_PERCENTAGE, 50)
        self.assertEqual(config.CONTEXT_COMPACTION_TOKEN_THRESHOLD, 12345)
        self.assertEqual(config.CONTEXT_COMPACTION_MODEL, "synthetic-model")
        self.assertTrue(config.ENABLE_TOOL_PERMISSIONS)

    def test_routes_use_measured_paths_and_admin_configuration_dependency(self):
        source = ROUTER_PATH.read_text(encoding="utf-8")
        for marker in (
            '@router.get("/archived/count", response_model=int)',
            '"/folder/{folder_id}/list"',
            '@router.get("/shared", response_model=list[SharedChatResponse])',
            '@router.get("/config", response_model=ChatConfigForm)',
            '@router.post("/config", response_model=ChatConfigForm)',
            "user=Depends(get_admin_user)",
        ):
            self.assertIn(marker, source)

    def test_persistent_configuration_is_bound_into_application_state(self):
        config_source = CONFIG_PATH.read_text(encoding="utf-8")
        main_source = MAIN_PATH.read_text(encoding="utf-8")
        for name in (
            "ENABLE_CONTEXT_COMPACTION",
            "CONTEXT_COMPACTION_TOKEN_THRESHOLD",
            "CONTEXT_COMPACTION_PROMPT_TEMPLATE",
            "CONTEXT_COMPACTION_MODEL",
            "CONTEXT_COMPACTION_RETENTION_PERCENTAGE",
            "CONTEXT_COMPACTION_TOKEN_CAP",
            "ENABLE_TOOL_PERMISSIONS",
        ):
            self.assertIn(f"{name} = PersistentConfig(", config_source)
            self.assertIn(f"app.state.config.{name}", main_source)

    def test_database_queries_filter_by_owner_and_hide_share_grants(self):
        source = MODEL_PATH.read_text(encoding="utf-8")
        self.assertIn("filter_by(user_id=user_id, archived=True).count()", source)
        self.assertIn("folder_id=folder_id,\n                user_id=user_id", source)
        self.assertIn("Chat.user_id == user_id", source)
        self.assertIn("Chat.share_id.is_not(None)", source)
        shared_fields = self.models["SharedChatResponse"].model_fields
        self.assertNotIn("access_grants", shared_fields)


if __name__ == "__main__":
    unittest.main()
