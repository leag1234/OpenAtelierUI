# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused tests for the measured local folder-sharing contract."""

import ast
import pathlib
import time
import uuid
from typing import Any, Literal, Optional

import pytest
from pydantic import BaseModel, Field, ValidationError


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]


def _load_definitions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ]
    assert {node.name for node in definitions} == set(names)
    exec(
        compile(ast.Module(body=definitions, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace


model_types = _load_definitions(
    OPEN_WEBUI / "models" / "folders.py",
    {"folder_permission_for_grants", "FolderTable"},
    {
        "Any": Any,
        "FolderModel": Any,
        "Optional": Optional,
        "time": time,
        "uuid": uuid,
    },
)
router_types = _load_definitions(
    OPEN_WEBUI / "routers" / "folders.py",
    {"FolderAccessGrantsForm"},
    {
        "Any": Any,
        "BaseModel": BaseModel,
        "Field": Field,
        "Literal": Literal,
        "Optional": Optional,
    },
)


FolderTable = model_types["FolderTable"]
folder_permission_for_grants = model_types["folder_permission_for_grants"]
FolderAccessGrantsForm = router_types["FolderAccessGrantsForm"]


def test_baseline_and_current_grant_forms_are_accepted():
    baseline = FolderAccessGrantsForm.model_validate({"access_grants": []})
    current = FolderAccessGrantsForm.model_validate(
        {"access_grants": [], "share_mode": "continue"}
    )
    assert baseline.share_mode is None
    assert current.share_mode == "continue"
    with pytest.raises(ValidationError):
        FolderAccessGrantsForm.model_validate(
            {"access_grants": [], "share_mode": "replace"}
        )


def test_folder_grants_are_normalized_and_deduplicated():
    raw = [
        {
            "principal_type": "user",
            "principal_id": "reader-id",
            "permission": "read",
        },
        {
            "principal_type": "user",
            "principal_id": "reader-id",
            "permission": "read",
        },
        {
            "principal_type": "group",
            "principal_id": "writers-id",
            "permission": "write",
        },
    ]
    normalized = FolderTable._normalize_access_grants("folder-id", raw, 42)
    assert len(normalized) == 2
    assert {item["permission"] for item in normalized} == {"read", "write"}
    assert all(item["resource_type"] == "folder" for item in normalized)
    assert all(item["resource_id"] == "folder-id" for item in normalized)
    assert all(item["created_at"] == 42 for item in normalized)


@pytest.mark.parametrize(
    "grant",
    [
        None,
        {"principal_type": "user", "principal_id": "*", "permission": "read"},
        {
            "principal_type": "team",
            "principal_id": "team-id",
            "permission": "read",
        },
        {
            "principal_type": "user",
            "principal_id": "member-id",
            "permission": "admin",
        },
    ],
)
def test_unsupported_grants_are_refused_before_persistence(grant):
    with pytest.raises(ValueError):
        FolderTable._normalize_access_grants("folder-id", [grant], 42)


def test_write_wins_and_group_membership_is_explicit():
    grants = [
        {
            "principal_type": "user",
            "principal_id": "member-id",
            "permission": "read",
        },
        {
            "principal_type": "group",
            "principal_id": "writers-id",
            "permission": "write",
        },
    ]
    assert folder_permission_for_grants(grants, "member-id") == "read"
    assert folder_permission_for_grants(grants, "member-id", {"writers-id"}) == "write"
    assert folder_permission_for_grants(grants, "unrelated-id") is None
