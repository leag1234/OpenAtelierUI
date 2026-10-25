# SPDX-License-Identifier: BSD-3-Clause
"""Execute the real plugin completion branch with isolated policy/storage stubs."""

import ast
import asyncio
import pathlib
import sys
import types
import unittest
from unittest.mock import patch
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    for node in ast.walk(function):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.decorator_list = []
            node.returns = None
            node.type_comment = None
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                arg.annotation = None
    return function


class _GroupStore:
    members = {}

    @classmethod
    def get_groups_by_member_id(cls, user_id):
        return [SimpleNamespace(id=group_id) for group_id in cls.members.get(user_id, [])]


class _ModelStore:
    rows = {}
    reads = []

    @classmethod
    def get_model_by_id(cls, model_id):
        cls.reads.append(model_id)
        return cls.rows.get(model_id)


def _load_real_access_policy():
    has_access = _function(ROOT / "utils" / "access_control.py", "has_access")
    access_namespace = {"Groups": _GroupStore}
    exec(compile(ast.Module(body=[has_access], type_ignores=[]), "access_control.py", "exec"), access_namespace)

    check_access = _function(ROOT / "utils" / "models.py", "check_model_access")
    model_namespace = {"Models": _ModelStore, "has_access": access_namespace["has_access"]}
    exec(compile(ast.Module(body=[check_access], type_ignores=[]), "models.py", "exec"), model_namespace)
    return model_namespace["check_model_access"]


def _load_completion_function():
    function = _function(ROOT / "functions.py", "generate_function_chat_completion")
    effects = []

    def get_tools(*_args, **_kwargs):
        effects.append("tools")
        raise _StopAfterGuard()

    namespace = {
        "Models": _ModelStore,
        "get_tools": get_tools,
        "effects": effects,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "functions.py", "exec"), namespace)
    return namespace["generate_function_chat_completion"], effects


class _StopAfterGuard(Exception):
    pass


class PluginBaseAdmissionTests(unittest.TestCase):
    def setUp(self):
        _ModelStore.reads = []
        _ModelStore.rows = {
            "preset": SimpleNamespace(base_model_id="pipe-id", user_id="publisher", access_control={"read": {"user_ids": ["member"]}}),
            "pipe-id": SimpleNamespace(base_model_id=None, user_id="owner", access_control={"read": {"user_ids": ["member"]}}),
            "private-pipe": SimpleNamespace(base_model_id=None, user_id="owner", access_control={}),
            "public-pipe": SimpleNamespace(base_model_id=None, user_id="owner", access_control=None),
            "group-pipe": SimpleNamespace(base_model_id=None, user_id="owner", access_control={"read": {"group_ids": ["workshop"]}}),
        }
        _GroupStore.members = {"group-member": ["workshop"]}
        self.check_model_access = _load_real_access_policy()
        self.function, self.effects = _load_completion_function()
        self.config_module = types.ModuleType("open_webui.config")
        self.config_module.BYPASS_MODEL_ACCESS_CONTROL = False
        self.models_module = types.ModuleType("open_webui.utils.models")
        self.models_module.check_model_access = self.check_model_access
        self.open_webui_module = types.ModuleType("open_webui")
        self.utils_module = types.ModuleType("open_webui.utils")

    def _call(self, *, user_id="member", role="user", model_id="preset", stream=False, base_id="pipe-id"):
        selected = {"id": model_id, "pipe": {"type": "pipe"}}
        _ModelStore.rows[model_id] = SimpleNamespace(
            base_model_id=base_id,
            user_id="publisher",
            access_control={"read": {"user_ids": ["member"]}},
            params=SimpleNamespace(model_dump=lambda: {}),
        )
        with patch.dict(
            sys.modules,
            {
                "open_webui": self.open_webui_module,
                "open_webui.config": self.config_module,
                "open_webui.utils": self.utils_module,
                "open_webui.utils.models": self.models_module,
            },
        ):
            return asyncio.run(
                self.function(
                    request=SimpleNamespace(),
                    form_data={"model": model_id, "stream": stream, "messages": []},
                    user=SimpleNamespace(id=user_id, role=role, email="member@example.test", name="Member"),
                    models={model_id: selected},
                )
            )

    def test_plain_and_streaming_denials_happen_before_tool_or_pipe_effects(self):
        # The selected preset is readable, but its base plugin is private.
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                _ModelStore.rows["pipe-id"] = SimpleNamespace(
                    base_model_id=None, user_id="owner", access_control={}
                )
                with self.assertRaisesRegex(Exception, "Model not found"):
                    self._call(stream=streaming)
                self.assertEqual(self.effects, [])
                self.assertEqual(_ModelStore.reads, ["preset", "pipe-id"])
                _ModelStore.reads = []

    def test_base_owner_public_read_and_named_group_read_use_existing_policy(self):
        cases = (
            ("owner", "owner", "pipe-id"),
            ("member", "member", "pipe-id"),
            ("public-member", "anyone", "public-pipe"),
            ("group-member", "anyone", "group-pipe"),
        )
        for user_id, owner, base_id in cases:
            with self.subTest(user_id=user_id, base_id=base_id):
                _ModelStore.rows["preset"] = SimpleNamespace(
                    base_model_id=base_id, user_id="publisher",
                    access_control={"read": {"user_ids": [user_id]}},
                )
                if owner != "anyone":
                    _ModelStore.rows[base_id].user_id = owner
                else:
                    _ModelStore.rows[base_id].user_id = "owner"
                _ModelStore.reads = []
                self.effects.clear()
                with self.assertRaises(_StopAfterGuard):
                    self._call(user_id=user_id, base_id=base_id)
                self.assertEqual(_ModelStore.reads, ["preset", base_id])
                self.assertEqual(self.effects, ["tools"])

    def test_missing_or_revoked_base_is_denied_on_the_next_call(self):
        _ModelStore.rows["pipe-id"] = SimpleNamespace(
            base_model_id=None, user_id="owner", access_control={}
        )
        with self.assertRaisesRegex(Exception, "Model not found"):
            self._call()
        self.assertEqual(self.effects, [])

        _ModelStore.rows["pipe-id"].access_control = {"read": {"user_ids": ["member"]}}
        self.effects.clear()
        with self.assertRaises(_StopAfterGuard):
            self._call()
        self.assertEqual(self.effects, ["tools"])

    def test_admin_bare_plugin_and_global_bypass_keep_existing_behavior(self):
        # Admin bypass remains, and a bare plugin row with no base rewrite is
        # not subjected to a second access check by this guard.
        for role, base_id, bypass in (
            ("admin", "pipe-id", False),
            ("user", None, False),
            ("user", "pipe-id", True),
        ):
            with self.subTest(role=role, base_id=base_id, bypass=bypass):
                self.config_module.BYPASS_MODEL_ACCESS_CONTROL = bypass
                _ModelStore.reads = []
                self.effects.clear()
                with self.assertRaises(_StopAfterGuard):
                    self._call(role=role, model_id="bare", base_id=base_id)
                # The first read is the selected row lookup. The base row is
                # read only when ordinary-user policy is active.
                self.assertEqual(_ModelStore.reads, ["bare"] if (role == "admin" or base_id is None or bypass) else ["bare", base_id])
                self.assertEqual(self.effects, ["tools"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
