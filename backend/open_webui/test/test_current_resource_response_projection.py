# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Check additive current response fields without importing the application."""

import ast
import importlib.util
import pathlib
import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
HELPER_SPEC = importlib.util.spec_from_file_location(
    "openatelier_access_grants",
    OPEN_WEBUI / "utils" / "access_grants.py",
)
access_grants = importlib.util.module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(access_grants)


class FileMetadataResponse(BaseModel):
    id: str


class UserResponse(BaseModel):
    id: str


def load_classes(path, names):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in names
    ]
    if {node.name for node in classes} != set(names):
        raise AssertionError(f"missing response classes from {path}")
    namespace = {
        "BaseModel": BaseModel,
        "ConfigDict": ConfigDict,
        "Field": Field,
        "FileMetadataResponse": FileMetadataResponse,
        "Optional": Optional,
        "UserResponse": UserResponse,
        "access_grants_for_acl": access_grants.access_grants_for_acl,
        "computed_field": computed_field,
    }
    exec(
        compile(ast.Module(body=classes, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace


knowledge = load_classes(
    OPEN_WEBUI / "models" / "knowledge.py",
    {
        "KnowledgeModel",
        "KnowledgeUserModel",
        "KnowledgeResponse",
        "KnowledgeUserResponse",
    },
)
prompts = load_classes(
    OPEN_WEBUI / "models" / "prompts.py",
    {"PromptModel", "PromptResponse", "PromptUserResponse"},
)


class CurrentResourceResponseProjectionTests(unittest.TestCase):
    def test_knowledge_keeps_legacy_acl_and_adds_current_grants(self):
        response = knowledge["KnowledgeResponse"](
            id="knowledge-01",
            user_id="owner",
            name="Community workshop",
            description="Synthetic corpus",
            access_control={"read": {"user_ids": ["member-01"]}},
            created_at=123,
            updated_at=124,
        ).model_dump()
        self.assertEqual(
            response["access_control"],
            {"read": {"user_ids": ["member-01"]}},
        )
        self.assertEqual(response["access_grants"][0]["principal_id"], "member-01")
        self.assertEqual(response["access_grants"][0]["resource_id"], "knowledge-01")

    def test_knowledge_private_and_public_projection(self):
        base = {
            "id": "knowledge-01",
            "user_id": "owner",
            "name": "Community workshop",
            "description": "Synthetic corpus",
            "created_at": 123,
            "updated_at": 124,
        }
        private = knowledge["KnowledgeResponse"](**base, access_control={}).model_dump()
        public = knowledge["KnowledgeResponse"](**base, access_control=None).model_dump()
        self.assertEqual(private["access_grants"], [])
        self.assertEqual(public["access_grants"][0]["principal_id"], "*")

    def test_prompt_adds_current_fields_without_removing_legacy_fields(self):
        response = prompts["PromptResponse"](
            command="community-workshop",
            user_id="owner",
            title="Community workshop",
            content="Synthetic prompt",
            timestamp=123,
            access_control={},
        ).model_dump()
        self.assertEqual(response["title"], "Community workshop")
        self.assertEqual(response["name"], "Community workshop")
        self.assertEqual(response["timestamp"], 123)
        self.assertEqual(response["created_at"], 123)
        self.assertEqual(response["updated_at"], 123)
        self.assertEqual(response["access_control"], {})
        self.assertEqual(response["access_grants"], [])
        self.assertEqual(response["data"], {})
        self.assertEqual(response["meta"], {})
        self.assertEqual(response["tags"], [])
        self.assertTrue(response["is_active"])


if __name__ == "__main__":
    unittest.main()
