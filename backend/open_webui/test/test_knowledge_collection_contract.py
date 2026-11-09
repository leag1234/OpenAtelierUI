# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused form and grant tests for local knowledge organization."""

import pytest

from open_webui.models.knowledge import (
    FileManifestEntry,
    KnowledgeDirectoryUpdateForm,
    KnowledgeFileMoveForm,
    SyncCleanupForm,
    SyncDiffForm,
)
from open_webui.utils.access_grants import (
    knowledge_access_control_for_grants,
    knowledge_access_grants_for_acl,
)


def test_knowledge_read_and_write_grants_round_trip_independently():
    grants = [
        {
            "principal_type": "user",
            "principal_id": "reader-id",
            "permission": "read",
        },
        {
            "principal_type": "user",
            "principal_id": "writer-id",
            "permission": "write",
        },
    ]
    acl = knowledge_access_control_for_grants(grants)
    assert acl == {
        "read": {"user_ids": ["reader-id"]},
        "write": {"user_ids": ["writer-id"]},
    }
    projected = knowledge_access_grants_for_acl(acl, "knowledge", "knowledge-id", 42)
    assert [(item["principal_id"], item["permission"]) for item in projected] == [
        ("reader-id", "read"),
        ("writer-id", "write"),
    ]
    assert all(item["resource_id"] == "knowledge-id" for item in projected)


@pytest.mark.parametrize(
    "grant",
    [
        {"principal_type": "user", "principal_id": "member", "permission": "execute"},
        {"principal_type": "group", "principal_id": "*", "permission": "read"},
        {"principal_type": "user", "principal_id": "*", "permission": "write"},
    ],
)
def test_knowledge_invalid_grants_are_refused(grant):
    with pytest.raises(ValueError):
        knowledge_access_control_for_grants([grant])


def test_directory_parent_omission_is_distinct_from_root_move():
    omitted = KnowledgeDirectoryUpdateForm.model_validate({"name": "Saved"})
    root = KnowledgeDirectoryUpdateForm.model_validate({"parent_id": None})
    assert omitted.parent_id == "__unset__"
    assert root.parent_id is None


def test_sync_and_move_forms_preserve_bounded_explicit_fields():
    move = KnowledgeFileMoveForm(file_id="file-id", directory_id="directory-id")
    cleanup = SyncCleanupForm(file_ids=["stale-file"])
    diff = SyncDiffForm(
        manifest=[
            FileManifestEntry(
                filename="notes.txt",
                path="Workshop/notes.txt",
                checksum="0" * 64,
                size=12,
            )
        ]
    )
    assert move.directory_id == "directory-id"
    assert cleanup.dir_ids == []
    assert diff.manifest[0].path == "Workshop/notes.txt"
