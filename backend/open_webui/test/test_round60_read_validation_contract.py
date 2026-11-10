# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused contract tests for the Round 60 compatibility declarations."""

import sqlite3

import pytest
from pydantic import ValidationError

from open_webui.models.automations import Automations
from open_webui.models.models import ModelResponse
from open_webui.routers.configs import InterfaceVisibilityForm
from open_webui.routers.images import ConnectionVerificationForm
from open_webui.routers.knowledge import KnowledgeUploadValidationForm


def test_model_response_projects_grants_and_hides_legacy_acl():
    value = ModelResponse.model_validate(
        {
            "id": "synthetic-model",
            "user_id": "owner",
            "name": "Synthetic model",
            "params": {},
            "meta": {},
            "access_control": None,
            "is_active": True,
            "updated_at": 2,
            "created_at": 1,
        }
    ).model_dump()
    assert "access_control" not in value
    assert value["access_grants"][0]["principal_id"] == "*"


def test_visibility_values_are_strict_booleans():
    assert InterfaceVisibilityForm(visibility={"models": True}).visibility == {
        "models": True
    }
    with pytest.raises(ValidationError):
        InterfaceVisibilityForm(visibility={"models": "yes"})


def test_connection_and_knowledge_forms_preserve_measured_validation():
    connection = ConnectionVerificationForm(
        engine="comfyui", url="http://example.invalid", key=None
    )
    assert connection.key is None
    upload = KnowledgeUploadValidationForm(count=0)
    assert upload.file_size is None
    assert upload.removed_file_ids == []
    with pytest.raises(ValidationError):
        KnowledgeUploadValidationForm(count=-1)


def test_automation_run_lookup_is_scoped_to_its_automation(tmp_path):
    database = tmp_path / "round60.db"
    sqlite3.connect(database).close()
    store = Automations("sqlite:///" + str(database))
    first = store.create("owner", "First", None, "{}", None, False, None)
    second = store.create("owner", "Second", None, "{}", None, False, None)
    run = store.record_run(first["id"], "error", error="Synthetic failure")
    assert store.get_run(first["id"], run["id"])["error"] == "Synthetic failure"
    assert store.get_run(second["id"], run["id"]) is None
