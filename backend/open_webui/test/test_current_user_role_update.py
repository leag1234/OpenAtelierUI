# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-bound controls for the measured current administrator role update."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "routers" / "users.py"
MODEL_PATH = ROOT / "models" / "users.py"


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Router:
    def post(self, *_args, **_kwargs):
        return lambda function: function


def load_update_route(namespace):
    tree = ast.parse(ROUTER_PATH.read_text(encoding="utf-8"), filename=str(ROUTER_PATH))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_user_by_id"
    )
    function.decorator_list = []
    module = ast.Module(body=[function], type_ignores=[])
    exec(compile(module, str(ROUTER_PATH), "exec"), namespace)
    return namespace["update_user_by_id"]


class UserRoleUpdateTests(unittest.TestCase):
    def setUp(self):
        self.effects = []
        self.target = SimpleNamespace(
            id="member-01",
            name="Member",
            email="member@example.invalid",
            profile_image_url="/user.png",
            role="user",
        )
        self.first = SimpleNamespace(id="admin-01")
        effects = self.effects
        target = self.target

        class Users:
            @staticmethod
            def get_user_by_id(user_id):
                return target if user_id == target.id else None

            @staticmethod
            def get_user_by_email(_email):
                return None

            @staticmethod
            def get_first_user():
                return self.first

            @staticmethod
            def update_user_by_id(user_id, values):
                effects.append(("user", user_id, dict(values)))
                for key, value in values.items():
                    setattr(target, key, value)
                return target

        class Auths:
            @staticmethod
            def update_user_password_by_id(user_id, _hashed):
                effects.append(("password", user_id))

            @staticmethod
            def update_email_by_id(user_id, email):
                effects.append(("email", user_id, email))

        namespace = {
            "router": Router(),
            "Optional": __import__("typing").Optional,
            "UserModel": object,
            "UserUpdateForm": object,
            "Depends": lambda value: value,
            "get_admin_user": object(),
            "Users": Users,
            "Auths": Auths,
            "get_password_hash": lambda value: "hashed:" + value,
            "log": SimpleNamespace(debug=lambda *_args: None),
            "HTTPException": HTTPException,
            "status": SimpleNamespace(
                HTTP_400_BAD_REQUEST=400,
                HTTP_403_FORBIDDEN=403,
                HTTP_422_UNPROCESSABLE_ENTITY=422,
            ),
            "ERROR_MESSAGES": SimpleNamespace(
                EMAIL_TAKEN="email taken",
                ACTION_PROHIBITED="action prohibited",
                DEFAULT=lambda: "default error",
                USER_NOT_FOUND="user not found",
            ),
        }
        self.route = load_update_route(namespace)

    def form(self, role):
        return SimpleNamespace(
            name="Member",
            email="member@example.invalid",
            profile_image_url="/user.png",
            password=None,
            role=role,
        )

    def test_current_update_persists_pending_role(self):
        result = asyncio.run(self.route("member-01", self.form("pending"), object()))
        self.assertEqual(result.role, "pending")
        write = next(effect for effect in self.effects if effect[0] == "user")
        self.assertEqual(write[2]["role"], "pending")

    def test_omitted_role_preserves_existing_role(self):
        result = asyncio.run(self.route("member-01", self.form(None), object()))
        self.assertEqual(result.role, "user")
        write = next(effect for effect in self.effects if effect[0] == "user")
        self.assertNotIn("role", write[2])

    def test_unknown_role_fails_before_writes(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(self.route("member-01", self.form("owner"), object()))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(self.effects, [])

    def test_first_administrator_cannot_be_demoted(self):
        self.first.id = "member-01"
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(self.route("member-01", self.form("pending"), object()))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.effects, [])

    def test_form_declares_optional_role(self):
        tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"), filename=str(MODEL_PATH))
        form = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "UserUpdateForm"
        )
        self.assertIn("role", {node.target.id for node in form.body if isinstance(node, ast.AnnAssign)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
