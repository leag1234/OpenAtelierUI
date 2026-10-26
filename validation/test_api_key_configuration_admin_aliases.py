#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Installed FastAPI/Pydantic checks for measured API-key admin aliases."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import AliasChoices, BaseModel, Field, model_validator


CANDIDATE = Path(__file__).resolve().parents[1]
AUTH_ROUTER = CANDIDATE / "backend/open_webui/routers/auths.py"


class TrackedConfig:
    def __init__(self):
        self.writes = 0
        self.values = {
            "SHOW_ADMIN_DETAILS": False,
            "WEBUI_URL": "https://example.invalid",
            "ENABLE_SIGNUP": False,
            "ENABLE_LOGIN_FORM": True,
            "ENABLE_API_KEY": True,
            "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS": False,
            "API_KEY_ALLOWED_ENDPOINTS": "",
            "DEFAULT_USER_ROLE": "user",
            "JWT_EXPIRES_IN": "4w",
            "ENABLE_COMMUNITY_SHARING": False,
            "ENABLE_MESSAGE_RATING": False,
            "ENABLE_CHANNELS": False,
            "ENABLE_USER_WEBHOOKS": False,
        }

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name, value):
        if name in {"writes", "values"}:
            object.__setattr__(self, name, value)
            return
        self.writes += 1
        self.values[name] = value


def build_actual_routes():
    tree = ast.parse(AUTH_ROUTER.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AdminConfig":
            selected.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name in {
            "get_admin_config",
            "update_admin_config",
        }:
            selected.append(node)
    namespace = {
        "AliasChoices": AliasChoices,
        "APIRouter": APIRouter,
        "BaseModel": BaseModel,
        "Depends": Depends,
        "Field": Field,
        "Request": Request,
        "model_validator": model_validator,
        "re": re,
        "router": APIRouter(),
        "get_admin_user": lambda: {"role": "admin"},
    }
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(AUTH_ROUTER), "exec"), namespace)
    app = FastAPI()
    app.include_router(namespace["router"], prefix="/auths")
    return app, namespace


def form_payload(*, current_names: bool, enabled: bool = False):
    api_key_fields = (
        {
            "ENABLE_API_KEYS": enabled,
            "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS": False,
            "API_KEYS_ALLOWED_ENDPOINTS": "",
        }
        if current_names
        else {
            "ENABLE_API_KEY": enabled,
            "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS": False,
            "API_KEY_ALLOWED_ENDPOINTS": "",
        }
    )
    return {
        "SHOW_ADMIN_DETAILS": False,
        "WEBUI_URL": "https://example.invalid",
        "ENABLE_SIGNUP": False,
        **api_key_fields,
        "DEFAULT_USER_ROLE": "user",
        "JWT_EXPIRES_IN": "4w",
        "ENABLE_COMMUNITY_SHARING": False,
        "ENABLE_MESSAGE_RATING": False,
        "ENABLE_CHANNELS": False,
        "ENABLE_USER_WEBHOOKS": False,
    }


class AdminConfigAliasTests(unittest.TestCase):
    def setUp(self):
        self.app, self.namespace = build_actual_routes()
        self.config = TrackedConfig()
        self.app.state.config = self.config
        self.client = TestClient(self.app)

    def test_get_returns_both_alias_sets_with_identical_values(self):
        response = self.client.get("/auths/admin/config")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        pairs = (
            ("ENABLE_API_KEY", "ENABLE_API_KEYS"),
            (
                "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS",
                "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS",
            ),
            ("API_KEY_ALLOWED_ENDPOINTS", "API_KEYS_ALLOWED_ENDPOINTS"),
        )
        for old, current in pairs:
            self.assertEqual(body[old], body[current])
        self.assertTrue(body["ENABLE_API_KEYS"])
        self.assertEqual(self.config.writes, 0)

    def test_post_accepts_current_and_legacy_names_and_returns_both(self):
        for current_names in (False, True):
            with self.subTest(current_names=current_names):
                self.config.writes = 0
                response = self.client.post(
                    "/auths/admin/config",
                    json=form_payload(current_names=current_names, enabled=False),
                )
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertFalse(body["ENABLE_API_KEY"])
                self.assertFalse(body["ENABLE_API_KEYS"])
                self.assertFalse(body["ENABLE_API_KEY_ENDPOINT_RESTRICTIONS"])
                self.assertFalse(body["ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS"])
                self.assertEqual(body["API_KEY_ALLOWED_ENDPOINTS"], "")
                self.assertEqual(body["API_KEYS_ALLOWED_ENDPOINTS"], "")
                self.assertFalse(self.config.ENABLE_API_KEY)
                self.assertGreater(self.config.writes, 0)

    def test_consistent_dual_aliases_are_accepted(self):
        payload = form_payload(current_names=False, enabled=True)
        payload.update(
            {
                "ENABLE_API_KEYS": True,
                "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS": False,
                "API_KEYS_ALLOWED_ENDPOINTS": "",
            }
        )
        response = self.client.post("/auths/admin/config", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.config.ENABLE_API_KEY)

    def test_conflicting_aliases_fail_before_any_setting_write(self):
        conflicts = (
            ("ENABLE_API_KEY", "ENABLE_API_KEYS", False),
            (
                "ENABLE_API_KEY_ENDPOINT_RESTRICTIONS",
                "ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS",
                True,
            ),
            ("API_KEY_ALLOWED_ENDPOINTS", "API_KEYS_ALLOWED_ENDPOINTS", "/api/models"),
        )
        for old, current, conflicting_value in conflicts:
            with self.subTest(old=old):
                self.config.writes = 0
                payload = form_payload(current_names=False, enabled=True)
                payload[current] = conflicting_value
                before = dict(self.config.values)
                response = self.client.post("/auths/admin/config", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.config.writes, 0)
                self.assertEqual(self.config.values, before)

    def test_existing_admin_dependency_remains_bound_to_both_routes(self):
        methods = {
            (route.path, method): route
            for route in self.namespace["router"].routes
            for method in route.methods
        }
        for method in ("GET", "POST"):
            route = methods[("/admin/config", method)]
            self.assertEqual(len(route.dependant.dependencies), 1)
            self.assertIs(route.dependant.dependencies[0].call, self.namespace["get_admin_user"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
