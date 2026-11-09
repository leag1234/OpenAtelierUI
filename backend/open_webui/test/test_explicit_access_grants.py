# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Run the actual ToolForm and ModelForm definitions without app dependencies."""

import ast
import copy
import importlib.util
import pathlib
import unittest
from typing import Any, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, model_validator


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
HELPER_SPEC = importlib.util.spec_from_file_location(
    "openatelier_access_grants",
    OPEN_WEBUI / "utils" / "access_grants.py",
)
access_grants = importlib.util.module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(access_grants)
convert_explicit_access_grants = access_grants.convert_explicit_access_grants
access_grants_for_acl = access_grants.access_grants_for_acl
preserve_access_control_for_update = access_grants.preserve_access_control_for_update


def load_form_classes(path, requested, extra_namespace=None):
    """Compile selected classes verbatim from the checked-in model source."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in requested
    ]
    found = {node.name for node in classes}
    if found != set(requested):
        raise AssertionError(f"missing form classes from {path}: {set(requested) - found}")
    module = ast.Module(body=classes, type_ignores=[])
    namespace = {
        "BaseModel": BaseModel,
        "AliasChoices": AliasChoices,
        "Any": Any,
        "ConfigDict": ConfigDict,
        "Field": Field,
        "Optional": Optional,
        "model_validator": model_validator,
        "convert_explicit_access_grants": convert_explicit_access_grants,
    }
    if extra_namespace:
        namespace.update(extra_namespace)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


tool_types = load_form_classes(
    OPEN_WEBUI / "models/tools.py", {"ToolMeta", "ToolForm"}
)
model_types = load_form_classes(
    OPEN_WEBUI / "models/models.py", {"ModelParams", "ModelMeta", "ModelForm"}
)
knowledge_types = load_form_classes(OPEN_WEBUI / "models/knowledge.py", {"KnowledgeForm"})
prompt_types = load_form_classes(OPEN_WEBUI / "models/prompts.py", {"PromptForm"})
channel_types = load_form_classes(
    OPEN_WEBUI / "models/channels.py", {"ChannelForm"}
)
FORMS = (
    (
        "ToolForm",
        tool_types["ToolForm"],
        {
            "id": "tool-synthetic",
            "name": "Workshop helper",
            "content": "class Tools:\n    pass",
            "meta": {},
        },
    ),
    (
        "ModelForm",
        model_types["ModelForm"],
        {"id": "model-synthetic", "name": "Workshop model", "meta": {}, "params": {}},
    ),
)


class ExplicitAccessGrantFormTests(unittest.TestCase):
    def test_current_resource_grants_do_not_change_legacy_acl_requests(self):
        resources = (
            (
                knowledge_types["KnowledgeForm"],
                {"name": "Workshop knowledge", "description": "Synthetic corpus"},
            ),
        )
        targeted = {"read": {"user_ids": ["legacy-member"]}}
        for form_class, base in resources:
            with self.subTest(form=form_class.__name__, style="current"):
                current = form_class.model_validate(dict(base, access_grants=[]))
                self.assertEqual(current.access_control, {})
            with self.subTest(form=form_class.__name__, style="legacy-public"):
                public = form_class.model_validate(dict(base, access_control=None))
                self.assertIsNone(public.access_control)
            with self.subTest(form=form_class.__name__, style="legacy-targeted"):
                legacy = form_class.model_validate(
                    dict(base, access_control=copy.deepcopy(targeted))
                )
                self.assertEqual(legacy.access_control, targeted)

    def test_current_prompt_name_and_legacy_title_alias(self):
        payload = {
            "command": "workshop",
            "name": "Workshop prompt",
            "content": "Synthetic prompt",
            "access_grants": [],
        }
        form = prompt_types["PromptForm"].model_validate(payload)
        self.assertEqual(form.name, "Workshop prompt")
        self.assertEqual(form.access_grants, [])
        self.assertEqual(form.model_dump()["name"], "Workshop prompt")
        legacy = prompt_types["PromptForm"].model_validate(
            {"command": "workshop", "title": "Legacy title", "content": "Synthetic prompt"}
        )
        self.assertEqual(legacy.name, "Legacy title")

    def test_current_private_channel_is_not_made_public(self):
        payload = {
            "name": "Workshop channel",
            "description": "Synthetic discussion",
            "is_private": True,
            "user_ids": ["member-01"],
        }
        form = channel_types["ChannelForm"].model_validate(payload)
        self.assertEqual(form.access_control, {})
        self.assertNotIn("is_private", form.model_dump())
        self.assertNotIn("user_ids", form.model_dump())

        public = channel_types["ChannelForm"].model_validate(
            {**payload, "is_private": False}
        )
        self.assertIsNone(public.access_control)

    def test_channel_rejects_ambiguous_privacy_flag(self):
        with self.assertRaises(ValidationError):
            channel_types["ChannelForm"].model_validate(
                {"name": "Workshop channel", "is_private": "yes"}
            )

    def test_current_omission_and_legacy_acl_extras_are_private(self):
        for name, form_class, base in FORMS:
            with self.subTest(form=name, access_grants="omitted"):
                payload = dict(base)
                form = form_class.model_validate(payload)
                self.assertEqual(form.access_control, {})
                self.assertNotIn("access_grants", form.model_dump())
                self.assertEqual(payload, base)

            for legacy_acl in ({}, None, {"read": {"user_ids": ["legacy-user"]}}):
                with self.subTest(form=name, access_control=legacy_acl):
                    payload = dict(base, access_control=copy.deepcopy(legacy_acl))
                    before = copy.deepcopy(payload)
                    form = form_class.model_validate(payload)
                    self.assertEqual(form.access_control, {})
                    self.assertEqual(payload, before)

    def test_explicit_empty_and_null_are_private(self):
        for name, form_class, base in FORMS:
            for grants in (None, []):
                with self.subTest(form=name, grants=grants):
                    payload = dict(base, access_grants=grants)
                    before = copy.deepcopy(payload)
                    form = form_class.model_validate(payload)
                    self.assertEqual(form.access_control, {})
                    self.assertNotIn("access_grants", form.model_dump())
                    self.assertEqual(payload, before)

    def test_user_and_public_read_grants_map_to_existing_acl(self):
        for name, form_class, base in FORMS:
            cases = (
                ([{"principal_type": "user", "principal_id": "member-01", "permission": "read"}],
                 {"read": {"user_ids": ["member-01"]}}),
                ([{"principal_type": "group", "principal_id": "group-01", "permission": "read"}],
                 {"read": {"group_ids": ["group-01"]}}),
                ([
                    {"principal_type": "user", "principal_id": "member-01", "permission": "read"},
                    {"principal_type": "group", "principal_id": "group-01", "permission": "read"},
                 ], {"read": {"user_ids": ["member-01"], "group_ids": ["group-01"]}}),
                ([{"principal_type": "user", "principal_id": "*", "permission": "read"}], None),
            )
            for grants, expected in cases:
                with self.subTest(form=name, grants=grants):
                    form = form_class.model_validate(dict(base, access_grants=grants))
                    self.assertEqual(form.access_control, expected)

    def test_ordinary_update_preserves_existing_rights(self):
        for _, form_class, base in FORMS:
            acl = {"read": {"user_ids": ["member-01"]}}
            for grants in (None, []):
                form = form_class.model_validate(dict(base, access_grants=grants))
                payload = preserve_access_control_for_update(
                    form.model_dump(), acl
                )
                self.assertEqual(payload["access_control"], acl)
            public = preserve_access_control_for_update(
                form_class.model_validate(dict(base)).model_dump(), None
            )
            self.assertIsNone(public["access_control"])

    def test_old_acl_extra_cannot_override_explicit_private_grants(self):
        for _, form_class, base in FORMS:
            form = form_class.model_validate(
                dict(
                    base,
                    access_grants=[],
                    access_control={"read": {"user_ids": ["unrelated"]}},
                )
            )
            self.assertEqual(form.access_control, {})

    def test_supported_acl_metadata_projection(self):
        targeted = access_grants_for_acl(
            {"read": {"user_ids": ["member-01"]}}, "tool", "resource-01", 123
        )
        self.assertEqual(
            [{k: grant[k] for k in ("principal_type", "principal_id", "permission")} for grant in targeted],
            [{"principal_type": "user", "principal_id": "member-01", "permission": "read"}],
        )
        self.assertEqual(targeted[0]["resource_type"], "tool")
        self.assertEqual(targeted[0]["resource_id"], "resource-01")
        self.assertEqual(targeted[0]["created_at"], 123)
        self.assertEqual(access_grants_for_acl(None, "model", "resource-01")[0]["principal_id"], "*")
        self.assertEqual(access_grants_for_acl({}, "tool", "resource-01"), [])

    def test_named_group_read_grants_mix_with_user_read_and_roundtrip(self):
        grants = [
            {"principal_type": "user", "principal_id": "member-01", "permission": "read"},
            {"principal_type": "group", "principal_id": "group-01", "permission": "read"},
        ]
        expected_acl = {"read": {"user_ids": ["member-01"], "group_ids": ["group-01"]}}
        self.assertEqual(access_grants.access_control_for_grants(grants), expected_acl)
        projected = access_grants_for_acl(expected_acl, "tool", "resource-01", 123)
        self.assertEqual(
            [(grant["principal_type"], grant["principal_id"], grant["permission"]) for grant in projected],
            [("user", "member-01", "read"), ("group", "group-01", "read")],
        )
        self.assertTrue(all(grant["resource_id"] == "resource-01" for grant in projected))

    def test_group_wildcard_and_write_grants_remain_rejected(self):
        for grant in (
            {"principal_type": "group", "principal_id": "*", "permission": "read"},
            {"principal_type": "group", "principal_id": "group-01", "permission": "write"},
        ):
            with self.subTest(grant=grant), self.assertRaises(ValueError):
                access_grants.access_control_for_grants([grant])

    def test_roundtrip_metadata_is_not_used_as_authority(self):
        for _, form_class, base in FORMS:
            grant = {
                "id": "client-controlled-id",
                "resource_type": "tool",
                "resource_id": "other-resource",
                "created_at": 9999999999,
                "principal_type": "user",
                "principal_id": "member-01",
                "permission": "read",
            }
            form = form_class.model_validate(dict(base, access_grants=[grant]))
            self.assertEqual(form.access_control, {"read": {"user_ids": ["member-01"]}})

    def test_malformed_and_unsupported_grants_are_rejected(self):
        invalid = [
            ("scalar grants", {"access_grants": "member-01"}),
            ("non-object grant", {"access_grants": [None]}),
            (
                "missing permission",
                {"access_grants": [{"principal_type": "user", "principal_id": "member-01"}]},
            ),
            (
                "extra field",
                {
                    "access_grants": [
                        {
                            "principal_type": "user",
                            "principal_id": "member-01",
                            "permission": "read",
                            "scope": "all",
                        }
                    ]
                },
            ),
            (
                "write permission",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "member-01", "permission": "write"}
                    ]
                },
            ),
            (
                "mixed read and write",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "member-01", "permission": "read"},
                        {"principal_type": "user", "principal_id": "member-02", "permission": "write"},
                    ]
                },
            ),
            (
                "empty ID",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "", "permission": "read"}
                    ]
                },
            ),
            (
                "whitespace-only ID",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "   ", "permission": "read"}
                    ]
                },
            ),
            (
                "duplicate ID",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "member-01", "permission": "read"},
                        {"principal_type": "user", "principal_id": "member-01", "permission": "read"},
                    ]
                },
            ),
        ]
        for name, form_class, base in FORMS:
            for case, changes in invalid:
                with self.subTest(form=name, invalid=case):
                    payload = dict(base, **copy.deepcopy(changes))
                    before = copy.deepcopy(payload)
                    with self.assertRaises(ValidationError) as caught:
                        form_class.model_validate(payload)
                    if case == "scalar grants":
                        self.assertIn(
                            "must be null or a list of read grants",
                            str(caught.exception),
                        )
                    self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
