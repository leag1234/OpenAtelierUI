#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Extracted old-source controls for API-key session-promotion refusal."""

from __future__ import annotations

import ast
import pathlib
import types
import unittest

from fastapi import HTTPException, Request, Response, status
from fastapi.background import BackgroundTasks
from fastapi.security import HTTPAuthorizationCredentials


ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTH_SOURCE = ROOT / "backend/open_webui/utils/auth.py"
ROUTER_SOURCE = ROOT / "backend/open_webui/routers/auths.py"


def extract_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    found = {node.name for node in nodes}
    if found != set(names):
        raise AssertionError("source functions missing: %s" % sorted(set(names) - found))
    # Execute actual function bodies against installed framework primitives;
    # route registration and unrelated application imports are not recreated.
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


def extract_assignments(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
    found = {
        target.id
        for node in nodes
        for target in node.targets
        if isinstance(target, ast.Name) and target.id in names
    }
    if found != set(names):
        raise AssertionError("source assignments missing: %s" % sorted(set(names) - found))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def request_for(path="/api/v1/auths/"):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("synthetic", 1234),
        "server": ("synthetic", 80),
        "state": {"enable_api_key": True},
        "app": types.SimpleNamespace(state=types.SimpleNamespace(config=types.SimpleNamespace(
            ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False,
            API_KEY_ALLOWED_ENDPOINTS="",
            JWT_EXPIRES_IN="30m",
            USER_PERMISSIONS={},
        ))),
    }
    return Request(scope)


class ApiKeySessionBoundaryTests(unittest.TestCase):
    def make_auth(self, user=None, *, epochs=False, restrictions=False):
        class UsersStub:
            active_user = user
            lookup_count = 0
            activity_count = 0

            @classmethod
            def get_user_by_api_key(cls, value):
                cls.lookup_count += 1
                return cls.active_user if value == "sk-" + "a" * 32 else None

            @classmethod
            def update_user_last_active_by_id(cls, _user_id):
                cls.activity_count += 1

        request_ns = {
            "Request": Request,
            "BackgroundTasks": BackgroundTasks,
            "HTTPAuthorizationCredentials": HTTPAuthorizationCredentials,
            "Depends": lambda dependency: dependency,
            "HTTPException": HTTPException,
            "status": status,
            "ERROR_MESSAGES": types.SimpleNamespace(
                API_KEY_NOT_ALLOWED="API key cannot create a session",
                INVALID_TOKEN="Invalid token",
            ),
            "Users": UsersStub,
            "_session_epochs_enabled": lambda: epochs,
            "bearer_security": object(),
            "decode_token": lambda _token: None,
        }
        extract_assignments(
            AUTH_SOURCE,
            {
                "_VALIDATED_API_KEY_STATE_MARKER",
                "_VALIDATED_API_KEY_STATE_ATTRIBUTE",
                "_VALIDATED_SESSION_STATE_ATTRIBUTE",
            },
            request_ns,
        )
        return extract_functions(
            AUTH_SOURCE,
            {"get_current_user_by_api_key", "get_current_user", "is_validated_api_key_request"},
            request_ns,
        )

    def make_route(self, auth, *, profile_enabled=False, session_context=None):
        effects = {"create": 0, "permissions": 0}

        def create_token(**_kwargs):
            effects["create"] += 1
            return "new-session-token"

        def get_permissions(*_args):
            effects["permissions"] += 1
            return {"synthetic": True}

        route_ns = {
            "Request": Request,
            "Response": Response,
            "Depends": lambda dependency: dependency,
            "get_current_user": auth["get_current_user"],
            "get_validated_session_context": lambda _request, _user_id: session_context,
            "is_validated_api_key_request": auth["is_validated_api_key_request"],
            "HTTPException": HTTPException,
            "status": status,
            "ERROR_MESSAGES": types.SimpleNamespace(API_KEY_NOT_ALLOWED="API key cannot create a session"),
            "parse_duration": lambda _value: None,
            "create_token": create_token,
            "get_permissions": get_permissions,
            "PROFILE_FIELDS_ENABLED": profile_enabled,
            "time": types.SimpleNamespace(time=lambda: 100),
            "datetime": __import__("datetime"),
            "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
            "WEBUI_AUTH_COOKIE_SECURE": False,
        }
        extract_functions(ROUTER_SOURCE, {"get_session_user"}, route_ns)
        return route_ns["get_session_user"], effects

    def test_successful_native_key_lookup_sets_private_identity_marker(self):
        user = types.SimpleNamespace(id="synthetic-user")
        auth = self.make_auth(user, epochs=False)
        request = request_for()
        result = auth["get_current_user"](
            request,
            BackgroundTasks(),
            HTTPAuthorizationCredentials(scheme="Bearer", credentials="sk-" + "a" * 32),
        )
        self.assertIs(result, user)
        self.assertTrue(auth["is_validated_api_key_request"](request, "synthetic-user"))
        self.assertFalse(auth["is_validated_api_key_request"](request, "other-user"))
        self.assertEqual(auth["Users"].lookup_count, 1)
        self.assertEqual(auth["Users"].activity_count, 1)
        self.assertNotIn("sk-", repr(request.state._openatelier_validated_api_key_user))

    def test_key_refusal_paths_leave_no_validated_marker(self):
        for label, enabled, restrictions, key, path in (
            ("disabled", False, False, "sk-" + "a" * 32, "/api/v1/auths/"),
            ("restricted", True, True, "sk-" + "a" * 32, "/api/v1/auths/"),
            ("invalid", True, False, "sk-" + "b" * 32, "/api/v1/auths/"),
        ):
            with self.subTest(label=label):
                user = types.SimpleNamespace(id="synthetic-user")
                auth = self.make_auth(None if label == "invalid" else user, epochs=False)
                request = request_for(path)
                request.state.enable_api_key = enabled
                request.app.state.config.ENABLE_API_KEY_ENDPOINT_RESTRICTIONS = restrictions
                request.app.state.config.API_KEY_ALLOWED_ENDPOINTS = "/api/v1/chat/completions"
                with self.assertRaises(HTTPException) as caught:
                    auth["get_current_user"](
                        request,
                        BackgroundTasks(),
                        HTTPAuthorizationCredentials(scheme="Bearer", credentials=key),
                    )
                self.assertEqual(
                    caught.exception.status_code,
                    401 if label == "invalid" else 403,
                )
                self.assertFalse(auth["is_validated_api_key_request"](request, "synthetic-user"))

    def test_marker_is_reset_at_dependency_entry_with_epochs_on_or_off(self):
        for epochs in (False, True):
            with self.subTest(epochs=epochs):
                auth = self.make_auth(epochs=epochs)
                request = request_for()
                request.state._openatelier_validated_api_key_user = (
                    auth["_VALIDATED_API_KEY_STATE_MARKER"], "old-user"
                )
                with self.assertRaises(HTTPException):
                    auth["get_current_user"](request, BackgroundTasks(), None)
                self.assertIsNone(request.state._openatelier_validated_api_key_user)

    def test_invalid_or_wrong_identity_marker_does_not_claim_key_context(self):
        auth = self.make_auth(epochs=False)
        request = request_for()
        request.state._openatelier_validated_api_key_user = (object(), "synthetic-user")
        self.assertFalse(auth["is_validated_api_key_request"](request, "synthetic-user"))
        request.state._openatelier_validated_api_key_user = (
            auth["_VALIDATED_API_KEY_STATE_MARKER"], "other-user"
        )
        self.assertFalse(auth["is_validated_api_key_request"](request, "synthetic-user"))
        request.state._openatelier_validated_api_key_user = (auth["_VALIDATED_API_KEY_STATE_MARKER"], "")
        self.assertFalse(auth["is_validated_api_key_request"](request, ""))

    def test_session_route_refuses_before_token_cookie_or_profile_projection(self):
        for profile_enabled in (False, True):
            for epochs in (False, True):
                with self.subTest(profile_enabled=profile_enabled, epochs=epochs):
                    user = types.SimpleNamespace(
                        id="synthetic-user", email="user@example.invalid", name="Synthetic",
                        role="user", profile_image_url="", bio="", gender="", date_of_birth=None,
                    )
                    auth = self.make_auth(user, epochs=epochs)
                    request = request_for()
                    auth["get_current_user"](
                        request,
                        BackgroundTasks(),
                        HTTPAuthorizationCredentials(scheme="Bearer", credentials="sk-" + "a" * 32),
                    )
                    route, effects = self.make_route(auth, profile_enabled=profile_enabled)
                    response = Response()
                    with self.assertRaises(HTTPException) as caught:
                        import asyncio
                        asyncio.run(route(request, response, user))
                    self.assertEqual(caught.exception.status_code, 403)
                    self.assertEqual(effects, {"create": 0, "permissions": 0})
                    self.assertNotIn(b"set-cookie", dict(response.raw_headers))

    def test_validated_jwt_context_keeps_original_session_token_and_cookie(self):
        auth = self.make_auth(epochs=True)
        user = types.SimpleNamespace(
            id="synthetic-user", email="user@example.invalid", name="Synthetic",
            role="user", profile_image_url="", bio="", gender="", date_of_birth=None,
        )
        original = ("original-jwt", 1790000000, "synthetic-stamp")
        route, effects = self.make_route(auth, profile_enabled=True, session_context=original)
        request = request_for()
        # An untrusted/stale marker cannot alter the valid JWT context.
        request.state._openatelier_validated_api_key_user = (object(), user.id)
        response = Response()
        import asyncio
        result = asyncio.run(route(request, response, user))
        self.assertEqual(result["token"], "original-jwt")
        self.assertEqual(result["expires_at"], 1790000000)
        self.assertEqual(effects, {"create": 0, "permissions": 1})
        self.assertIn(b"token=original-jwt", dict(response.raw_headers)[b"set-cookie"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
