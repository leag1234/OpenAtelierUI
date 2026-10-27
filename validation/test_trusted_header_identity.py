#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Run the actual trusted signin branch with isolated in-memory account stubs."""

import ast
import asyncio
import datetime
import pathlib
import re
import time
import types
import unittest
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/auths.py"


class RouteError(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class Headers:
    def __init__(self, entries=()):
        self.entries = list(entries)

    def getlist(self, name):
        return [value for key, value in self.entries if key.lower() == name.lower()]

    def __contains__(self, name):
        return bool(self.getlist(name))

    def __getitem__(self, name):
        values = self.getlist(name)
        if not values:
            raise KeyError(name)
        return values[-1]

    def get(self, name, default=None):
        values = self.getlist(name)
        return values[-1] if values else default


def extract_functions(namespace, names):
    tree = ast.parse(ROUTER.read_text(encoding="utf-8"), filename=str(ROUTER))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    if {node.name for node in selected} != set(names):
        raise AssertionError("expected actual route function was not found")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTER), "exec"), namespace)
    return namespace


class TrustedHeaderIdentityTests(unittest.TestCase):
    def setUp(self):
        self.users = {}
        self.effects = {"inserted": [], "signup_calls": 0, "cookies": []}
        users = self
        effects = self.effects

        class UsersStub:
            @staticmethod
            def get_user_by_email(email):
                return users.users.get(email)

            @staticmethod
            def get_num_users():
                return len(users.users)

        class AuthsStub:
            @staticmethod
            def insert_new_auth(*args, **kwargs):
                if args:
                    email, password, name, _image, role = args
                else:
                    email = kwargs["email"]
                    password = kwargs["password"]
                    name = kwargs["name"]
                    role = kwargs["role"]
                effects["inserted"].append({"email": email, "password": password, "name": name, "role": role})
                user = types.SimpleNamespace(id="synthetic-id", email=email, name=name,
                                             role=role, profile_image_url="/user.png")
                users.users[email] = user
                return user

            @staticmethod
            def authenticate_user_by_trusted_header(email):
                return users.users.get(email)

            @staticmethod
            def authenticate_user(email, _password):
                return users.users.get(email)

        class Response:
            def set_cookie(self, **kwargs):
                effects["cookies"].append(kwargs)

        def signup(*_args, **_kwargs):
            effects["signup_calls"] += 1
            raise AssertionError("trusted provisioning must not call public signup")

        namespace = {
            "HTTPException": RouteError,
            "Request": object,
            "Response": object,
            "SigninForm": object,
            "SignupForm": lambda **values: types.SimpleNamespace(
                profile_image_url="/user.png", **values
            ),
            "status": types.SimpleNamespace(HTTP_400_BAD_REQUEST=400, HTTP_403_FORBIDDEN=403),
            "ERROR_MESSAGES": types.SimpleNamespace(
                INVALID_TRUSTED_HEADER="Invalid trusted identity header.",
                ACCESS_PROHIBITED="Access prohibited.",
                CREATE_USER_ERROR="User creation failed.",
                INVALID_CRED="Invalid credentials.",
                INVALID_EMAIL_FORMAT="Invalid email format.",
                EMAIL_TAKEN="Email taken.",
                PASSWORD_TOO_LONG="Password too long.",
                EXISTING_USERS="Existing users prevent bootstrap.",
            ),
            "WEBUI_AUTH_TRUSTED_EMAIL_HEADER": "X-Trusted-Email",
            "WEBUI_AUTH_TRUSTED_NAME_HEADER": "X-Trusted-Name",
            "WEBUI_AUTH_TRUSTED_ROLE_HEADER": "X-Trusted-Role",
            "WEBUI_AUTH": True,
            "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
            "WEBUI_AUTH_COOKIE_SECURE": False,
            "Users": UsersStub,
            "Auths": AuthsStub,
            "validate_email_format": lambda value: re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None,
            "get_password_hash": lambda value: "hashed:" + value,
            "uuid": uuid,
            "log": types.SimpleNamespace(error=lambda *_args: None),
            "parse_duration": lambda _value: None,
            "create_password_session_token": lambda *_args: None,
            "create_token": lambda **_kwargs: "synthetic-session-token",
            "get_permissions": lambda *_args: {"features": {}},
            "time": time,
            "datetime": datetime,
            "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
            "WEBUI_AUTH_COOKIE_SECURE": False,
            "signup": signup,
        }
        extract_functions(namespace, {"_trusted_header_user", "signin", "signup"})
        actual_signup = namespace["signup"]

        async def tracked_signup(*args, **kwargs):
            effects["signup_calls"] += 1
            return await actual_signup(*args, **kwargs)

        namespace["signup"] = tracked_signup
        self.namespace = namespace
        self.Response = Response

    def _request(self, entries=(), *, signup_enabled=False):
        config = types.SimpleNamespace(
            JWT_EXPIRES_IN=None,
            USER_PERMISSIONS={},
            WEBHOOK_URL=None,
            ENABLE_SIGNUP=signup_enabled,
            ENABLE_LOGIN_FORM=signup_enabled,
            DEFAULT_USER_ROLE="pending",
        )
        return types.SimpleNamespace(
            headers=Headers(entries),
            app=types.SimpleNamespace(state=types.SimpleNamespace(config=config, WEBUI_NAME="MdkLinux")),
        )

    def _signin(self, entries=(), *, signup_enabled=False, **form_values):
        form = types.SimpleNamespace(email=form_values.get("email", "existing@example.invalid"),
                                     password="synthetic-password")
        response = self.Response()
        result = asyncio.run(self.namespace["signin"](
            self._request(entries, signup_enabled=signup_enabled), response, form))
        return result, response

    def _expect_error(self, entries=(), status_code=400):
        try:
            self._signin(entries)
        except RouteError as error:
            self.assertEqual(error.status_code, status_code)
            return error
        self.fail("expected trusted signin refusal")

    def test_first_trusted_user_is_created_as_user_while_public_signup_is_disabled(self):
        result, response = self._signin([
            ("X-Trusted-Email", "member@example.invalid"),
            ("X-Trusted-Name", "Synthetic Member"),
            ("X-Trusted-Role", "user"),
        ])
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["email"], "member@example.invalid")
        self.assertEqual(len(self.users), 1)
        self.assertEqual(self.effects["inserted"][0]["role"], "user")
        self.assertTrue(self.effects["inserted"][0]["password"].startswith("hashed:"))
        self.assertEqual(self.effects["signup_calls"], 0)
        self.assertEqual(self.effects["cookies"][0]["value"], result["token"])
        self.assertTrue(self.effects["cookies"][0]["httponly"])

    def test_missing_duplicate_invalid_and_unconfigured_identity_refuse_without_writes(self):
        self._expect_error([], 400)
        for entries in (
            [("X-Trusted-Email", "bad-email"), ("X-Trusted-Role", "user")],
            [("X-Trusted-Email", "member@example.invalid"), ("X-Trusted-Email", "member@example.invalid"),
             ("X-Trusted-Role", "user")],
            [("X-Trusted-Email", "member@example.invalid"), ("X-Trusted-Name", "bad\nname"),
             ("X-Trusted-Role", "user")],
            [("X-Trusted-Email", "member@example.invalid"), ("X-Trusted-Role", "unknown")],
            [("X-Trusted-Email", "member@example.invalid")],
            [("X-Trusted-Email", "member@example.invalid"), ("X-Trusted-Role", "user"),
             ("X-Trusted-Role", "user")],
        ):
            with self.subTest(entries=entries):
                self._expect_error(entries)
        self.assertEqual((self.users, self.effects["inserted"]), ({}, []))

        self.namespace["WEBUI_AUTH_TRUSTED_ROLE_HEADER"] = None
        self._expect_error([("X-Trusted-Email", "member@example.invalid")], 403)
        self.assertEqual((self.users, self.effects["inserted"]), ({}, []))

    def test_explicit_admin_header_can_provision_first_admin(self):
        result, _response = self._signin([("X-Trusted-Email", "first@example.invalid"),
                                          ("X-Trusted-Role", "admin")])
        self.assertEqual(result["role"], "admin")
        self.assertEqual(self.effects["inserted"][0]["role"], "admin")

    def test_first_admin_without_configured_role_keeps_old_signup_gate(self):
        self.namespace["WEBUI_AUTH_TRUSTED_ROLE_HEADER"] = None
        self._expect_error([("X-Trusted-Email", "first@example.invalid")], 403)
        self.assertEqual((self.users, self.effects["inserted"]), ({}, []))
        result, _response = self._signin([("X-Trusted-Email", "first@example.invalid")], signup_enabled=True)
        self.assertEqual(result["role"], "admin")
        self.assertEqual(self.effects["inserted"][-1]["role"], "admin")
        self.assertEqual(self.effects["signup_calls"], 2)

    def test_existing_identity_never_gains_or_loses_role_from_header(self):
        self.users["member@example.invalid"] = types.SimpleNamespace(
            id="existing-id", email="member@example.invalid", name="Member", role="user",
            profile_image_url="/user.png")
        result, _response = self._signin([("X-Trusted-Email", "member@example.invalid"),
                                          ("X-Trusted-Role", "admin")])
        self.assertEqual(result["role"], "user")
        self.assertEqual(self.effects["inserted"], [])

    def test_unconfigured_trusted_header_preserves_password_signin_path(self):
        self.namespace["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] = ""
        self.users["existing@example.invalid"] = types.SimpleNamespace(
            id="existing-id", email="existing@example.invalid", name="Existing", role="user",
            profile_image_url="/user.png")
        result, _response = self._signin([], email="existing@example.invalid")
        self.assertEqual(result["id"], "existing-id")
        self.assertEqual(self.effects["inserted"], [])

    def test_actual_public_signup_stays_disabled(self):
        form = types.SimpleNamespace(email="new@example.invalid", password="synthetic", name="New")
        with self.assertRaises(RouteError) as caught:
            asyncio.run(self.namespace["signup"](self._request(), self.Response(), form))
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual((self.users, self.effects["inserted"]), ({}, []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
