# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused form and safety tests for the measured Skills compatibility slice."""

import pytest

from open_webui.models.skills import (
    MAX_SKILL_BYTES,
    SkillForm,
    _files_from_form,
    _normalize_grants,
)


def test_legacy_content_and_current_files_share_one_canonical_representation():
    legacy = SkillForm.model_validate(
        {"id": "workshop", "name": "Workshop", "content": "# Workshop"}
    )
    assert _files_from_form(legacy) == [{"path": "SKILL.md", "content": "# Workshop"}]

    current = SkillForm.model_validate(
        {
            "id": "workshop",
            "name": "Workshop",
            "files": [
                {"path": "SKILL.md", "content": "# Workshop"},
                {"path": "references/checklist.md", "content": "Synthetic checklist"},
            ],
        }
    )
    assert [item["path"] for item in _files_from_form(current)] == [
        "SKILL.md",
        "references/checklist.md",
    ]


@pytest.mark.parametrize("path", ["/absolute", "../escape", "nested/../escape", ""])
def test_unsafe_paths_are_refused(path):
    form = SkillForm.model_validate(
        {"id": "unsafe", "name": "Unsafe", "files": [{"path": path, "content": "x"}]}
    )
    with pytest.raises(ValueError, match="invalid_skill_path"):
        _files_from_form(form)


def test_duplicate_and_oversized_files_are_refused():
    duplicate = SkillForm.model_validate(
        {
            "id": "duplicate",
            "name": "Duplicate",
            "files": [
                {"path": "SKILL.md", "content": "one"},
                {"path": "SKILL.md", "content": "two"},
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicate_skill_path"):
        _files_from_form(duplicate)

    oversized = SkillForm.model_validate(
        {
            "id": "oversized",
            "name": "Oversized",
            "content": "x" * (MAX_SKILL_BYTES + 1),
        }
    )
    with pytest.raises(ValueError, match="skill_size_limit"):
        _files_from_form(oversized)


def test_file_operations_are_bounded_and_reversible():
    form = SkillForm.model_validate(
        {
            "id": "operations",
            "name": "Operations",
            "operations": [
                {
                    "op": "move",
                    "path": "references/old.md",
                    "destination": "references/new.md",
                },
                {"op": "put", "path": "notes.md", "content": "Synthetic notes"},
                {"op": "delete", "path": "discard.md"},
            ],
        }
    )
    existing = {
        "files": [
            {"path": "SKILL.md", "content": "# Operations"},
            {"path": "references/old.md", "content": "Reference"},
            {"path": "discard.md", "content": "Discard"},
        ]
    }
    files = _files_from_form(form, existing)
    assert {item["path"] for item in files} == {
        "SKILL.md",
        "references/new.md",
        "notes.md",
    }


def test_invalid_access_permission_is_refused_before_persistence():
    with pytest.raises(ValueError, match="invalid_grant"):
        _normalize_grants(
            "skill-id",
            [
                {
                    "principal_type": "user",
                    "principal_id": "member-id",
                    "permission": "execute",
                }
            ],
            1,
        )

    grants = _normalize_grants(
        "skill-id",
        [
            {
                "principal_type": "user",
                "principal_id": "member-id",
                "permission": "read",
            }
        ],
        1,
    )
    assert grants[0]["resource_type"] == "skill"
    assert grants[0]["resource_id"] == "skill-id"
