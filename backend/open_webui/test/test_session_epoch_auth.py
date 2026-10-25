# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound tests for the default-off session epoch auth integration."""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "utils"))
import session_epochs  # noqa: E402


class HTTPError(Exception):
    def __init__(self, status_code=None, detail=None, **kwargs):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Credentials:
    def __init__(self, scheme, credentials):
        self.scheme = scheme
        self.credentials = credentials


class BackgroundTasks:
    def __init__(self, events):
        self.events = events

    def add_task(self, function, *args):
        self.events.append(("background", function, args))


class UsersStub:
    def __init__(self, users, events):
        self.users = users
        self.events = events
        self.api_key_reads = []

    def get_user_by_id(self, user_id):
        self.events.append("user_lookup")
        return self.users.get(user_id)

    def get_user_by_api_key(self, key):
        self.api_key_reads.append(key)
        return types.SimpleNamespace(id="api-user", role="user")

    def update_user_last_active_by_id(self, user_id):
        self.events.append(("last_active", user_id))


class JWTStub:
    def __init__(self, events):
        self.events = events
        self.encoded = []
        self.decoded = {}

    def encode(self, payload, _secret, algorithm):
        token = f"signed-{len(self.encoded) + 1}"
        self.encoded.append((token, dict(payload), algorithm))
        self.decoded[token] = dict(payload)
        return token

    def decode(self, token, _secret, algorithms):
        self.events.append("decode")
        if token not in self.decoded:
            raise ValueError("invalid synthetic signature or expiry")
        if algorithms != ["HS256"]:
            raise AssertionError("unexpected JWT algorithms")
        return dict(self.decoded[token])


def _function_nodes(path: Path, names: set[str]):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if {node.name for node in selected} != names:
        raise AssertionError(f"missing requested functions from {path}: {names}")
    for node in selected:
        node.decorator_list = []
        node.returns = None
        for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            arg.annotation = None
    return selected


def _route_decorator(path: Path, function_name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return ast.unparse(node.decorator_list[0])


def _make_namespace(database_url: str, *, users=None, events=None):
    events = [] if events is None else events
    users = UsersStub({"synthetic-owner": types.SimpleNamespace(id="synthetic-owner", role="user")}, events) if users is None else users
    jwt_stub = JWTStub(events)
    access_denied = "Access prohibited"
    messages = types.SimpleNamespace(
        API_KEY_NOT_ALLOWED="API key not allowed",
        INVALID_TOKEN="Invalid token",
        UNAUTHORIZED="Unauthorized",
        ACCESS_PROHIBITED=access_denied,
    )
    status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_403_FORBIDDEN=403)
    namespace = {
        "os": os,
        "Path": Path,
        "unquote": unquote,
        "DATABASE_URL": database_url,
        "HTTPException": HTTPError,
        "status": status,
        "ERROR_MESSAGES": messages,
        "Users": users,
        "get_current_user_by_api_key": users.get_user_by_api_key,
        "BackgroundTasks": BackgroundTasks,
        "HTTPAuthorizationCredentials": Credentials,
        "Depends": lambda dependency: dependency,
        "bearer_security": object(),
        "SESSION_SECRET": "synthetic-test-key",
        "ALGORITHM": "HS256",
        "jwt": jwt_stub,
        "uuid": __import__("uuid"),
        "timedelta": timedelta,
        "datetime": datetime,
        "UTC": timezone.utc,
        "MAX_EPOCH_BYTES": session_epochs.MAX_EPOCH_BYTES,
        "MAX_EPOCH_CHARS": session_epochs.MAX_EPOCH_CHARS,
        "SessionEpochError": session_epochs.SessionEpochError,
        "issue_epoch": session_epochs.issue_epoch,
        "validate_epoch": session_epochs.validate_epoch,
        "rotate_epoch": session_epochs.rotate_epoch,
        "events": events,
    }
    auth_path = ROOT / "utils" / "auth.py"
    auth_names = {
        "_session_epochs_enabled",
        "_session_epoch_database_path",
        "_session_state_unavailable",
        "_valid_session_stamp_claim",
        "revoke_user_session_epoch",
        "create_token",
        "decode_token",
        "get_current_user",
        "get_admin_user",
    }
    exec(compile(ast.Module(body=_function_nodes(auth_path, auth_names), type_ignores=[]), str(auth_path), "exec"), namespace)
    namespace["jwt"].events = events
    component_validate = namespace["validate_epoch"]

    def validate_epoch(*args, **kwargs):
        events.append("validate")
        return component_validate(*args, **kwargs)

    namespace["validate_epoch"] = validate_epoch
    return namespace


def _request(cookie=None, *, path="/api/v1/users/me", api_key_enabled=False):
    return types.SimpleNamespace(
        cookies={} if cookie is None else {"token": cookie},
        state=types.SimpleNamespace(enable_api_key=api_key_enabled),
        url=types.SimpleNamespace(path=path),
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=False,
                    API_KEY_ALLOWED_ENDPOINTS="",
                )
            )
        ),
    )


def _prepared_db(path: Path):
    source = path.with_name("old-source.db")
    db = sqlite3.connect(source)
    db.executescript("""
      CREATE TABLE auth (
        id VARCHAR(255) PRIMARY KEY,
        email TEXT NOT NULL,
        password TEXT NOT NULL,
        active INTEGER NOT NULL
      );
      INSERT INTO auth VALUES ('synthetic-owner','owner@example.invalid','bcrypt-placeholder',1);
    """)
    db.commit()
    db.close()
    session_epochs.prepare_auth_session_stamp_copy(source, path)


class SessionEpochAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "prepared.db"
        _prepared_db(self.database)
        self.events = []
        self.users = UsersStub(
            {"synthetic-owner": types.SimpleNamespace(id="synthetic-owner", role="user")},
            self.events,
        )
        self.namespace = _make_namespace(
            "sqlite:///" + str(self.database), users=self.users, events=self.events
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_flag_off_preserves_jwt_payload_and_user_lookup_path(self):
        payload = {"id": "synthetic-owner", "custom": {"keep": True}}
        original = {"id": "synthetic-owner", "custom": {"keep": True}}
        with patch.dict(os.environ, {}, clear=True):
            token = self.namespace["create_token"](payload)
            self.assertEqual(payload, original)
            encoded = self.namespace["jwt"].encoded[-1][1]
            self.assertEqual(encoded, original)
            self.assertNotIn("session_stamp", encoded)
            self.assertNotIn("jti", encoded)
            self.namespace["jwt"].decoded[token] = {"id": "synthetic-owner"}
            user = self.namespace["get_current_user"](
                _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
            )
            self.assertIs(user, self.users.users["synthetic-owner"])
            self.assertEqual(self.events, ["decode", "user_lookup", ("background", self.users.update_user_last_active_by_id, ("synthetic-owner",))])

    def test_enabled_issuance_preserves_expiry_payload_and_adds_stable_stamp_fresh_jti(self):
        payload = {"id": "synthetic-owner", "custom": ["unchanged"]}
        original = {"id": "synthetic-owner", "custom": ["unchanged"]}
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            first = self.namespace["create_token"](payload, expires_delta=timedelta(minutes=5))
            second = self.namespace["create_token"](payload, expires_delta=timedelta(minutes=5))
        first_payload = self.namespace["jwt"].decoded[first]
        second_payload = self.namespace["jwt"].decoded[second]
        self.assertEqual(payload, original)
        self.assertIn("exp", first_payload)
        self.assertIsInstance(first_payload["exp"], datetime)
        self.assertEqual(first_payload["session_stamp"], second_payload["session_stamp"])
        self.assertNotEqual(first_payload["jti"], second_payload["jti"])
        self.assertEqual(self.namespace["jwt"].encoded[-1][2], "HS256")

    def test_decode_precedes_stamp_check_and_user_lookup_and_revoke_invalidates(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "1"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            self.events.clear()
            user = self.namespace["get_current_user"](
                _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
            )
            self.assertIs(user, self.users.users["synthetic-owner"])
            self.assertEqual(self.events[:3], ["decode", "validate", "user_lookup"])

            old_stamp = self.namespace["jwt"].decoded[token]["session_stamp"]
            self.assertIsNone(self.namespace["revoke_user_session_epoch"]("synthetic-owner"))
            self.assertFalse(session_epochs.validate_epoch(self.database, "synthetic-owner", old_stamp))
            with self.assertRaises(HTTPError) as stale:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
            self.assertEqual((stale.exception.status_code, stale.exception.detail), (401, "Invalid token"))

    def test_missing_malformed_claims_and_unavailable_store_fail_closed(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "on"}, clear=True):
            self.namespace["DATABASE_URL"] = "sqlite+pysqlite:///" + str(self.database)
            self.assertEqual(self.namespace["_session_epoch_database_path"](), self.database)
            for payload in (
                {"id": "synthetic-owner"},
                {"id": "synthetic-owner", "session_stamp": ""},
                {"id": "synthetic-owner", "session_stamp": "x" * 513},
            ):
                token = f"claim-{len(self.namespace['jwt'].decoded)}"
                self.namespace["jwt"].decoded[token] = payload
                with self.subTest(payload_keys=tuple(payload)):
                    with self.assertRaises(HTTPError) as caught:
                        self.namespace["get_current_user"](
                            _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                        )
                    self.assertEqual((caught.exception.status_code, caught.exception.detail), (401, "Invalid token"))

            for database_url in (
                "postgresql://unsupported/example",
                "sqlite:///:memory:",
                "sqlite:///relative.db",
                "sqlite:////tmp/session.db?mode=ro",
            ):
                self.namespace["DATABASE_URL"] = database_url
                with self.subTest(database_url=database_url):
                    with self.assertRaises(HTTPError) as unavailable:
                        self.namespace["create_token"]({"id": "synthetic-owner"})
                    self.assertEqual((unavailable.exception.status_code, unavailable.exception.detail), (503, "Session state unavailable"))

    def test_missing_or_corrupt_prepared_database_is_service_unavailable(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            self.namespace["DATABASE_URL"] = "sqlite:///" + str(self.root / "missing.db")
            with self.assertRaises(HTTPError) as caught:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
            self.assertEqual((caught.exception.status_code, caught.exception.detail), (503, "Session state unavailable"))

    def test_deleted_auth_row_preserves_invalid_token_status(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            db = sqlite3.connect(self.database)
            db.execute("DELETE FROM auth WHERE id='synthetic-owner'")
            db.commit()
            db.close()
            with self.assertRaises(HTTPError) as caught:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (401, "Invalid token"))

    def test_malformed_and_locked_store_fail_generic503_then_recover(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            stamp = self.namespace["jwt"].decoded[token]["session_stamp"]
            db = sqlite3.connect(self.database)
            db.execute("UPDATE auth SET session_stamp='' WHERE id='synthetic-owner'")
            db.commit()
            db.close()
            with self.assertRaises(HTTPError) as malformed:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
            self.assertEqual((malformed.exception.status_code, malformed.exception.detail), (503, "Session state unavailable"))

            db = sqlite3.connect(self.database)
            db.execute("UPDATE auth SET session_stamp=? WHERE id='synthetic-owner'", (stamp,))
            db.commit()
            holder = sqlite3.connect(self.database, isolation_level=None)
            holder.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(HTTPError) as busy:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
            self.assertEqual((busy.exception.status_code, busy.exception.detail), (503, "Session state unavailable"))
            holder.rollback()
            holder.close()
            db.close()
            self.assertIs(
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                ),
                self.users.users["synthetic-owner"],
            )

    def test_api_key_branch_remains_separate_when_epochs_enabled(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            with self.assertRaises(HTTPError) as disabled:
                self.namespace["get_current_user"](
                    _request(api_key_enabled=False),
                    BackgroundTasks(self.events),
                    Credentials("Bearer", "sk-synthetic"),
                )
            self.assertEqual(disabled.exception.status_code, 403)
            user = self.namespace["get_current_user"](
                _request(api_key_enabled=True),
                BackgroundTasks(self.events),
                Credentials("Bearer", "sk-synthetic"),
            )
        self.assertEqual(user.id, "api-user")
        self.assertEqual(self.users.api_key_reads, ["sk-synthetic"])
        self.assertNotIn("decode", self.events)

    def test_failed_existing_decode_keeps_unauthorized_branch_before_epoch_lookup(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            with self.assertRaises(HTTPError) as caught:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", "bad-signature-or-expiry")
                )
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (401, "Unauthorized"))
        self.assertEqual(self.events, ["decode"])

    def test_revocation_route_uses_admin_dependency_and_exact_missing_target(self):
        path = ROOT / "routers" / "users.py"
        route_node = _function_nodes(path, {"revoke_user_sessions"})[0]
        namespace = {
            "Depends": lambda dependency: dependency,
            "get_admin_user": self.namespace["get_admin_user"],
            "revoke_user_session_epoch": self.namespace["revoke_user_session_epoch"],
        }
        exec(compile(ast.Module(body=[route_node], type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(_route_decorator(path, "revoke_user_sessions"), "router.post('/{user_id}/sessions/revoke')")
        admin = types.SimpleNamespace(id="administrator", role="admin")
        route = namespace["revoke_user_sessions"]
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            self.assertIsNone(asyncio.run(route("synthetic-owner", user=admin)))
            with self.assertRaises(HTTPError) as missing:
                asyncio.run(route("no-such-user", user=admin))
            self.assertEqual((missing.exception.status_code, missing.exception.detail), (404, "User not found"))
            member = types.SimpleNamespace(id="synthetic-owner", role="user")
            with self.assertRaises(HTTPError) as denied:
                self.namespace["get_admin_user"](member)
            self.assertEqual((denied.exception.status_code, denied.exception.detail), (401, "Access prohibited"))

    def test_disabled_revoke_route_is_an_explicit_service_state_refusal(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPError) as caught:
                self.namespace["revoke_user_session_epoch"]("synthetic-owner")
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (503, "Session state unavailable"))

    def test_post_signout_is_a_thin_alias_of_get_signout(self):
        path = ROOT / "routers" / "auths.py"
        functions = _function_nodes(path, {"signout", "signout_post"})
        namespace = {
            "ENABLE_OAUTH_SIGNUP": types.SimpleNamespace(value=False),
            "HTTPException": HTTPError,
        }
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
        self.assertEqual(_route_decorator(path, "signout_post"), "router.post('/signout')")

        class Response:
            def __init__(self):
                self.deleted = []
                self.headers = {}

            def delete_cookie(self, name):
                self.deleted.append(name)

        request = types.SimpleNamespace(cookies={})
        get_response = Response()
        post_response = Response()
        get_result = asyncio.run(namespace["signout"](request, get_response))
        post_result = asyncio.run(namespace["signout_post"](request, post_response))
        self.assertEqual(post_result, get_result)
        self.assertEqual(post_response.deleted, get_response.deleted)


if __name__ == "__main__":
    unittest.main(verbosity=2)
