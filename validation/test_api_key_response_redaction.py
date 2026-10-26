# SPDX-License-Identifier: BSD-3-Clause
"""Exercise actual FastAPI response filtering on the three user-model routes."""

import ast
import pathlib
import types
import unittest

from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from typing import Optional


ROOT = pathlib.Path(__file__).resolve().parents[1]
USERS_MODEL_SOURCE = ROOT / "backend/open_webui/models/users.py"
USERS_ROUTER_SOURCE = ROOT / "backend/open_webui/routers/users.py"


def _classes_from_model_source():
    tree = ast.parse(USERS_MODEL_SOURCE.read_text(encoding="utf-8"))
    wanted = {"UserSettings", "UserModel", "UserRoleUpdateForm", "UserUpdateForm"}
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name in wanted
    ]
    if {node.name for node in nodes} != wanted:
        raise AssertionError("expected user model classes were not found")
    namespace = {
        "BaseModel": BaseModel,
        "ConfigDict": ConfigDict,
        "Optional": Optional,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(USERS_MODEL_SOURCE), "exec"), namespace)
    return namespace


def _registered_test_app():
    user_models = _classes_from_model_source()
    tree = ast.parse(USERS_ROUTER_SOURCE.read_text(encoding="utf-8"))
    wanted = {"get_users", "update_user_role", "update_user_by_id"}
    endpoints = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted
    ]
    if {node.name for node in endpoints} != wanted:
        raise AssertionError("expected user endpoints were not found")

    member = user_models["UserModel"](
        id="synthetic-member",
        name="Community workshop member",
        email="member@synthetic.invalid",
        role="user",
        profile_image_url="/synthetic-member.png",
        last_active_at=1700000001,
        updated_at=1700000002,
        created_at=1700000000,
        api_key="synthetic-api-key-marker",
        settings=user_models["UserSettings"](
            ui={"theme": "dark"}, extra_setting={"enabled": True}
        ),
        info={"display_label": "Synthetic member", "custom": {"visible": True}},
        oauth_sub="synthetic-subject",
    )
    admin = types.SimpleNamespace(id="synthetic-admin")

    class UsersStub:
        @staticmethod
        def get_users(skip=None, limit=None):
            return [member]

        @staticmethod
        def get_user_by_id(user_id):
            return member if user_id == member.id else None

        @staticmethod
        def get_user_by_email(email):
            return None

        @staticmethod
        def get_first_user():
            return types.SimpleNamespace(id="synthetic-first-owner")

        @staticmethod
        def update_user_role_by_id(user_id, role):
            member.role = role
            return member

        @staticmethod
        def update_user_by_id(user_id, fields):
            for key, value in fields.items():
                setattr(member, key, value)
            return member

    class AuthsStub:
        @staticmethod
        def update_user_password_by_id(*args):
            return True

        @staticmethod
        def update_email_by_id(*args):
            return True

    class ErrorMessages:
        EMAIL_TAKEN = "Email taken"
        DEFAULT = staticmethod(lambda: "Error")
        USER_NOT_FOUND = "User not found"

    def get_admin_user():
        return admin

    router = APIRouter()
    namespace = {
        "router": router,
        "UserModel": user_models["UserModel"],
        "UserRoleUpdateForm": user_models["UserRoleUpdateForm"],
        "UserUpdateForm": user_models["UserUpdateForm"],
        "Optional": Optional,
        "Depends": __import__("fastapi").Depends,
        "get_admin_user": get_admin_user,
        "Users": UsersStub,
        "Auths": AuthsStub,
        "get_password_hash": lambda value: "synthetic-hash",
        "log": types.SimpleNamespace(debug=lambda *args: None),
        "ERROR_MESSAGES": ErrorMessages,
        "HTTPException": HTTPException,
        "status": status,
    }
    exec(compile(ast.Module(body=endpoints, type_ignores=[]), str(USERS_ROUTER_SOURCE), "exec"), namespace)
    app = FastAPI()
    app.include_router(router)
    return app, member


class UserApiKeyResponseRedactionTests(unittest.TestCase):
    def setUp(self):
        self.app, self.member = _registered_test_app()
        self.client = TestClient(self.app)
        self.expected = self.member.model_dump(mode="json", exclude={"api_key"})
        self.synthetic_key = self.member.api_key

    def tearDown(self):
        self.client.close()

    def assert_redacted_model(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("api_key", response.json())
        self.assertEqual(response.json(), self.expected)
        self.assertEqual(self.member.api_key, self.synthetic_key)

    def test_admin_user_list_excludes_only_api_key(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
        self.assertNotIn("api_key", response.json()[0])
        self.assertEqual(response.json()[0], self.expected)
        self.assertEqual(self.member.api_key, self.synthetic_key)

    def test_role_update_optional_model_response_excludes_only_api_key(self):
        response = self.client.post(
            "/update/role", json={"id": self.member.id, "role": "user"}
        )
        self.assert_redacted_model(response)

    def test_user_update_optional_model_response_excludes_only_api_key(self):
        response = self.client.post(
            f"/{self.member.id}/update",
            json={
                "name": self.member.name,
                "email": self.member.email,
                "profile_image_url": self.member.profile_image_url,
                "password": None,
            },
        )
        self.assert_redacted_model(response)

    def test_fastapi_route_metadata_limits_exclusion_to_three_routes(self):
        selected = {
            (route.path, next(iter(route.methods)))
            for route in self.app.routes
            if getattr(route, "response_model", None) is not None
            and getattr(route, "response_model_exclude", None)
        }
        self.assertEqual(
            selected,
            {
                ("/", "GET"),
                ("/update/role", "POST"),
                ("/{user_id}/update", "POST"),
            },
        )
        for route in self.app.routes:
            if (route.path, next(iter(route.methods))) in selected:
                expected_exclude = (
                    {"__all__": {"api_key"}}
                    if route.path == "/"
                    else {"api_key"}
                )
                self.assertEqual(route.response_model_exclude, expected_exclude)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UserApiKeyResponseRedactionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
