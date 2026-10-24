# SPDX-License-Identifier: BSD-3-Clause
"""Run the actual ToolForm and ModelForm definitions without app dependencies."""

import ast
import copy
import importlib.util
import pathlib
import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
HELPER_SPEC = importlib.util.spec_from_file_location(
    "openatelier_access_grants",
    OPEN_WEBUI / "utils" / "access_grants.py",
)
access_grants = importlib.util.module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(access_grants)
convert_explicit_access_grants = access_grants.convert_explicit_access_grants


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
        "ConfigDict": ConfigDict,
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
    def test_legacy_omission_and_access_control_are_preserved(self):
        for name, form_class, base in FORMS:
            with self.subTest(form=name, access_control="omitted"):
                payload = dict(base)
                form = form_class.model_validate(payload)
                self.assertIsNone(form.access_control)
                self.assertNotIn("access_grants", form.model_dump())
                self.assertEqual(payload, base)

            for legacy_acl in ({}, None, {"read": {"user_ids": ["legacy-user"]}}):
                with self.subTest(form=name, access_control=legacy_acl):
                    payload = dict(base, access_control=copy.deepcopy(legacy_acl))
                    before = copy.deepcopy(payload)
                    form = form_class.model_validate(payload)
                    self.assertEqual(form.access_control, legacy_acl)
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

    def test_nonempty_user_read_grants_fail_closed_with_actionable_error(self):
        grants = [
            {"principal_type": "user", "principal_id": "member-01", "permission": "read"}
        ]
        for name, form_class, base in FORMS:
            with self.subTest(form=name):
                payload = dict(base, access_grants=copy.deepcopy(grants))
                before = copy.deepcopy(payload)
                with self.assertRaises(ValidationError) as caught:
                    form_class.model_validate(payload)
                self.assertIn(
                    "cannot preserve per-principal source and rights semantics",
                    str(caught.exception),
                )
                self.assertEqual(payload, before)

    def test_mixed_vocabularies_and_unsupported_grants_are_rejected(self):
        invalid = [
            ("mixed old vocabulary", {"access_grants": None, "access_control": None}),
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
                "group principal",
                {
                    "access_grants": [
                        {"principal_type": "group", "principal_id": "group-01", "permission": "read"}
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
                "empty ID",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "", "permission": "read"}
                    ]
                },
            ),
            (
                "wildcard ID",
                {
                    "access_grants": [
                        {"principal_type": "user", "principal_id": "*", "permission": "read"}
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
                    with self.assertRaises(ValidationError):
                        form_class.model_validate(payload)
                    self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
