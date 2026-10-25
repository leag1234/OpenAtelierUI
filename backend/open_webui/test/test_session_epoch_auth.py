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
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

    def get_user_by_email(self, email):
        return next(
            (user for user in self.users.values() if user.email == email), None
        )

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
        INVALID_TOKEN="Your session has expired or the token is invalid. Please sign in again.",
        UNAUTHORIZED="Unauthorized",
        ACCESS_PROHIBITED=access_denied,
    )
    status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_403_FORBIDDEN=403)
    namespace = {
        "os": os,
        "Path": Path,
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
        "time": time,
        "timedelta": timedelta,
        "datetime": datetime,
        "UTC": timezone.utc,
        "MAX_EPOCH_BYTES": session_epochs.MAX_EPOCH_BYTES,
        "MAX_EPOCH_CHARS": session_epochs.MAX_EPOCH_CHARS,
        "_VALIDATED_SESSION_STATE_MARKER": object(),
        "_VALIDATED_SESSION_STATE_ATTRIBUTE": "_openatelier_validated_session_epoch",
        "_MAX_SESSION_EXPIRATION": 253402300799,
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
        "get_validated_session_context",
        "get_validated_session_epoch",
        "revoke_user_session_epoch",
        "create_token",
        "create_password_session_token",
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
                    JWT_EXPIRES_IN=None,
                    USER_PERMISSIONS={},
                )
            )
        ),
    )


def _prepared_db(path: Path):
    source = path.with_name(path.stem + ".old-source.db")
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


def _install_session_user_route(namespace):
    route_path = ROOT / "routers" / "auths.py"
    route_namespace = dict(namespace)
    route_namespace.update(
        {
            "datetime": __import__("datetime"),
            "time": __import__("time"),
            "parse_duration": lambda _value: None,
            "get_permissions": lambda *_args: {},
            "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
            "WEBUI_AUTH_COOKIE_SECURE": False,
        }
    )
    selected = _function_nodes(route_path, {"get_session_user"})
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(route_path), "exec"),
        route_namespace,
    )
    return route_namespace["get_session_user"]


def _install_password_signin_route(namespace, *, user, authenticated_user=None):
    route_path = ROOT / "routers" / "auths.py"
    users = types.SimpleNamespace(
        get_user_by_email=lambda _email: user,
        get_num_users=lambda: 1,
    )
    auths = types.SimpleNamespace(
        authenticate_user=lambda _email, _password: authenticated_user,
        authenticate_user_by_trusted_header=lambda _email: user,
    )
    route_namespace = dict(namespace)
    route_namespace.update(
        {
            "WEBUI_AUTH_TRUSTED_EMAIL_HEADER": "",
            "WEBUI_AUTH_TRUSTED_NAME_HEADER": "",
            "WEBUI_AUTH": True,
            "Users": users,
            "Auths": auths,
            "parse_duration": lambda value: timedelta(weeks=4) if value == "4w" else None,
            "get_permissions": lambda *_args: {"workspace": {"tools": False}},
            "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
            "WEBUI_AUTH_COOKIE_SECURE": False,
            "ERROR_MESSAGES": types.SimpleNamespace(
                INVALID_CRED="Invalid credentials",
                INVALID_TRUSTED_HEADER="Invalid trusted header",
            ),
            "HTTPException": HTTPError,
            "time": time,
            "datetime": __import__("datetime"),
        }
    )
    selected = _function_nodes(route_path, {"signin"})
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(route_path), "exec"),
        route_namespace,
        route_namespace,
    )
    return route_namespace["signin"], users, auths, route_namespace


def _signin_request(*, headers=None, expiry="4w"):
    return types.SimpleNamespace(
        headers={} if headers is None else headers,
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    JWT_EXPIRES_IN=expiry,
                    USER_PERMISSIONS={},
                )
            )
        ),
    )


class SessionEpochAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "prepared.db"
        _prepared_db(self.database)
        self.events = []
        self.users = UsersStub(
            {
                "synthetic-owner": types.SimpleNamespace(
                    id="synthetic-owner",
                    email="owner@example.invalid",
                    name="Synthetic Owner",
                    role="user",
                    profile_image_url="",
                )
            },
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

    def test_default_off_missing_user_keeps_configured_legacy_error_copy(self):
        with patch.dict(os.environ, {}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            self.users.users.clear()
            with self.assertRaises(HTTPError) as missing_user:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", token)
                )
        self.assertEqual(missing_user.exception.status_code, 401)
        self.assertEqual(
            missing_user.exception.detail,
            "Your session has expired or the token is invalid. Please sign in again.",
        )

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

    def test_successful_ordinary_password_login_emits_one_consistent_server_context(self):
        user = types.SimpleNamespace(
            id="synthetic-owner",
            email="owner@example.invalid",
            name="Synthetic Owner",
            role="user",
            profile_image_url="",
        )
        route, _users, _auths, _route_namespace = _install_password_signin_route(
            self.namespace, user=user, authenticated_user=user
        )
        cookie = {}
        response = types.SimpleNamespace(set_cookie=lambda **kwargs: cookie.update(kwargs))
        issuance_time = 1_790_000_000
        with (
            patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True),
            patch.object(time, "time", return_value=issuance_time) as clock,
        ):
            result = asyncio.run(
                route(
                    _signin_request(),
                    response,
                    types.SimpleNamespace(email=user.email, password="synthetic-only"),
                )
            )
        claims = self.namespace["jwt"].decoded[result["token"]]
        self.assertEqual(
            set(claims),
            {
                "id",
                "typ",
                "auth_method",
                "mfa_verified",
                "iat",
                "auth_time",
                "exp",
                "session_stamp",
                "jti",
            },
        )
        self.assertEqual(claims["typ"], "session")
        self.assertEqual(claims["auth_method"], "password")
        self.assertIs(claims["mfa_verified"], False)
        self.assertEqual(claims["iat"], issuance_time)
        self.assertEqual(claims["auth_time"], issuance_time)
        self.assertEqual(claims["exp"] - claims["iat"], 2_419_200)
        self.assertEqual(result["expires_at"], claims["exp"])
        self.assertEqual(cookie["value"], result["token"])
        self.assertEqual(clock.call_count, 1)

    def test_failed_password_and_default_off_password_do_not_add_context(self):
        user = types.SimpleNamespace(
            id="synthetic-owner",
            email="owner@example.invalid",
            name="Synthetic Owner",
            role="user",
            profile_image_url="",
        )
        failed_route, _users, _auths, _route_namespace = _install_password_signin_route(
            self.namespace, user=user, authenticated_user=None
        )
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            with self.assertRaises(HTTPError) as failed:
                asyncio.run(
                    failed_route(
                        _signin_request(),
                        types.SimpleNamespace(set_cookie=lambda **_kwargs: None),
                        types.SimpleNamespace(email=user.email, password="wrong"),
                    )
                )
        self.assertEqual(failed.exception.status_code, 400)
        self.assertEqual(self.namespace["jwt"].encoded, [])

        default_route, _users, _auths, default_namespace = _install_password_signin_route(
            self.namespace, user=user, authenticated_user=user
        )
        with patch.dict(os.environ, {}, clear=True):
            result = asyncio.run(
                default_route(
                    _signin_request(),
                    types.SimpleNamespace(set_cookie=lambda **_kwargs: None),
                    types.SimpleNamespace(email=user.email, password="synthetic-only"),
                )
            )
        default_claims = self.namespace["jwt"].decoded[result["token"]]
        self.assertEqual(set(default_claims), {"id", "exp"})

    def test_trusted_header_and_auth_disabled_signin_remain_unlabelled(self):
        user = types.SimpleNamespace(
            id="synthetic-owner",
            email="owner@example.invalid",
            name="Synthetic Owner",
            role="user",
            profile_image_url="",
        )
        for mode in ("trusted_header", "auth_disabled"):
            route, _users, _auths, route_namespace = _install_password_signin_route(
                self.namespace, user=user, authenticated_user=user
            )
            if mode == "trusted_header":
                route_namespace["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] = "X-Trusted-Email"
                headers = {"X-Trusted-Email": user.email}
            else:
                route_namespace["WEBUI_AUTH_TRUSTED_EMAIL_HEADER"] = ""
                route_namespace["WEBUI_AUTH"] = False
                headers = {}
            with self.subTest(mode=mode), patch.dict(
                os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True
            ):
                result = asyncio.run(
                    route(
                        _signin_request(headers=headers),
                        types.SimpleNamespace(set_cookie=lambda **_kwargs: None),
                        types.SimpleNamespace(email=user.email, password="synthetic-only"),
                    )
                )
            claims = self.namespace["jwt"].decoded[result["token"]]
            self.assertNotIn("typ", claims)
            self.assertNotIn("auth_method", claims)
            self.assertNotIn("mfa_verified", claims)
            self.assertNotIn("iat", claims)
            self.assertNotIn("auth_time", claims)

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

    def test_enabled_profile_rejects_explicit_wrong_purpose_before_epoch_or_user_lookup(self):
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            claims = dict(self.namespace["jwt"].decoded[token])
            for purpose in ("refresh", None, ["session"], 7):
                malformed = f"purpose-{len(self.namespace['jwt'].decoded)}"
                self.namespace["jwt"].decoded[malformed] = dict(claims, typ=purpose)
                request = _request()
                self.events.clear()
                with self.subTest(purpose=repr(purpose)):
                    with self.assertRaises(HTTPError) as caught:
                        self.namespace["get_current_user"](
                            request,
                            BackgroundTasks(self.events),
                            Credentials("Bearer", malformed),
                        )
                    self.assertEqual(
                        (caught.exception.status_code, caught.exception.detail),
                        (401, "Invalid token"),
                    )
                    self.assertEqual(self.events, ["decode"])
                    self.assertIsNone(
                        self.namespace["get_validated_session_context"](
                            request, "synthetic-owner"
                        )
                    )

            for expiration in (
                "1900000000",
                None,
                True,
                1.5,
                -1,
                253402300800,
                10**100,
            ):
                malformed_expiry = f"expiry-{len(self.namespace['jwt'].decoded)}"
                payload = dict(claims, typ="session", exp=expiration)
                self.namespace["jwt"].decoded[malformed_expiry] = payload
                self.events.clear()
                with self.subTest(expiration=repr(expiration)):
                    with self.assertRaises(HTTPError) as caught:
                        self.namespace["get_current_user"](
                            _request(),
                            BackgroundTasks(self.events),
                            Credentials("Bearer", malformed_expiry),
                        )
                    self.assertEqual(
                        (caught.exception.status_code, caught.exception.detail),
                        (401, "Invalid token"),
                    )
                    self.assertEqual(self.events, ["decode"])

    def test_marked_context_cannot_be_replayed_for_another_user_or_corrupted_expiry(self):
        route = _install_session_user_route(self.namespace)
        request = _request(path="/api/v1/auths/")
        response = types.SimpleNamespace(set_cookie=lambda **_kwargs: None)
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = self.namespace["create_token"]({"id": "synthetic-owner"})
            owner = self.namespace["get_current_user"](
                request, BackgroundTasks(self.events), Credentials("Bearer", token)
            )
            before = len(self.namespace["jwt"].encoded)
            with self.assertRaises(HTTPError) as cross_user:
                asyncio.run(
                    route(request, response, types.SimpleNamespace(id="other-user"))
                )
            self.assertEqual(
                (cross_user.exception.status_code, cross_user.exception.detail),
                (401, "Invalid token"),
            )
            validated = request.state._openatelier_validated_session_epoch
            request.state._openatelier_validated_session_epoch = (*validated[:4], True)
            with self.assertRaises(HTTPError) as malformed:
                asyncio.run(route(request, response, owner))
            self.assertEqual(
                (malformed.exception.status_code, malformed.exception.detail),
                (401, "Invalid token"),
            )
            self.assertEqual(len(self.namespace["jwt"].encoded), before)

    def test_auths_session_read_after_rotation_returns_only_old_token(self):
        route = _install_session_user_route(self.namespace)
        request = _request(path="/api/v1/auths/")
        cookie = {}
        response = types.SimpleNamespace(set_cookie=lambda **kwargs: cookie.update(kwargs))
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            original = self.namespace["create_token"]({"id": "synthetic-owner"})
            admitted = self.namespace["get_current_user"](
                request, BackgroundTasks(self.events), Credentials("Bearer", original)
            )
            expected = self.namespace["get_validated_session_epoch"](
                request, admitted.id
            )
            self.assertEqual(expected, self.namespace["jwt"].decoded[original]["session_stamp"])
            self.namespace["revoke_user_session_epoch"]("synthetic-owner")
            rotated = session_epochs.get_epoch(self.database, "synthetic-owner")
            encoded_before = len(self.namespace["jwt"].encoded)

            result = asyncio.run(route(request, response, admitted))

            self.assertEqual(result["token"], original)
            self.assertEqual(len(self.namespace["jwt"].encoded), encoded_before)
            self.assertEqual(cookie["value"], original)
            self.assertEqual(session_epochs.get_epoch(self.database, "synthetic-owner"), rotated)
            with self.assertRaises(HTTPError) as guarded_issuer:
                self.namespace["create_token"](
                    {"id": "synthetic-owner"}, expected_session_stamp=expected
                )
            self.assertEqual(
                (guarded_issuer.exception.status_code, guarded_issuer.exception.detail),
                (401, "Invalid token"),
            )
            with self.assertRaises(HTTPError) as old_token:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", original)
                )
            self.assertEqual((old_token.exception.status_code, old_token.exception.detail), (401, "Invalid token"))

            fresh = self.namespace["create_token"]({"id": "synthetic-owner"})
            fresh_user = self.namespace["get_current_user"](
                _request(), BackgroundTasks(self.events), Credentials("Bearer", fresh)
            )
            self.assertEqual(fresh_user.id, "synthetic-owner")

    def test_auths_session_read_preserves_exact_token_and_expiry_without_issuance(self):
        route = _install_session_user_route(self.namespace)
        request = _request(path="/api/v1/auths/")
        cookie = {}
        response = types.SimpleNamespace(set_cookie=lambda **kwargs: cookie.update(kwargs))
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "1"}, clear=True):
            expiration = 1_900_000_000
            self.namespace["create_token"]({"id": "synthetic-owner"})
            original = self.namespace["jwt"].encode(
                {
                    "id": "synthetic-owner",
                    "typ": "session",
                    "exp": expiration,
                    "session_stamp": session_epochs.get_epoch(
                        self.database, "synthetic-owner"
                    ),
                },
                "synthetic-test-key",
                algorithm="HS256",
            )
            admitted = self.namespace["get_current_user"](
                request, BackgroundTasks(self.events), Credentials("Bearer", original)
            )
            encoded_before = len(self.namespace["jwt"].encoded)
            returned = asyncio.run(route(request, response, admitted))
            self.assertEqual(returned["token"], original)
            self.assertEqual(returned["expires_at"], expiration)
            self.assertEqual(cookie["value"], original)
            self.assertEqual(
                cookie["expires"],
                datetime.fromtimestamp(expiration, timezone.utc),
            )
            self.assertEqual(len(self.namespace["jwt"].encoded), encoded_before)
            self.assertEqual(
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", original)
                ).id,
                "synthetic-owner",
            )

            self.namespace["revoke_user_session_epoch"]("synthetic-owner")
            with self.assertRaises(HTTPError) as revoked_after_read:
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", original)
                )
            self.assertEqual(
                (revoked_after_read.exception.status_code, revoked_after_read.exception.detail),
                (401, "Invalid token"),
            )
            fresh = self.namespace["create_token"]({"id": "synthetic-owner"})
            self.assertEqual(
                self.namespace["get_current_user"](
                    _request(), BackgroundTasks(self.events), Credentials("Bearer", fresh)
                ).id,
                "synthetic-owner",
            )

    def test_default_off_session_read_keeps_legacy_token_issuance_path(self):
        route = _install_session_user_route(self.namespace)
        request = _request(path="/api/v1/auths/")
        response = types.SimpleNamespace(set_cookie=lambda **_kwargs: None)
        with patch.dict(os.environ, {}, clear=True):
            original = self.namespace["create_token"]({"id": "synthetic-owner"})
            user = self.namespace["get_current_user"](
                request, BackgroundTasks(self.events), Credentials("Bearer", original)
            )
            encoded_before = len(self.namespace["jwt"].encoded)
            result = asyncio.run(route(request, response, user))
        self.assertNotEqual(result["token"], original)
        self.assertEqual(len(self.namespace["jwt"].encoded), encoded_before + 1)

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

    def test_literal_percent_escape_in_sqlite_filename_is_not_decoded(self):
        literal_database = self.root / "session%20literal.db"
        _prepared_db(literal_database)
        namespace = _make_namespace("sqlite:///" + str(literal_database))
        self.assertEqual(namespace["_session_epoch_database_path"](), literal_database)
        with patch.dict(os.environ, {"OPENATELIER_SESSION_EPOCHS": "true"}, clear=True):
            token = namespace["create_token"]({"id": "synthetic-owner"})
        self.assertTrue(literal_database.is_file())
        self.assertEqual(
            session_epochs.get_epoch(literal_database, "synthetic-owner"),
            namespace["jwt"].decoded[token]["session_stamp"],
        )

    def test_posix_resource_module_is_imported_only_by_measurement_helper(self):
        component_path = ROOT / "utils" / "session_epochs.py"
        tree = ast.parse(component_path.read_text(encoding="utf-8"))
        module_imports_resource = any(
            isinstance(node, ast.Import)
            and any(alias.name == "resource" for alias in node.names)
            or isinstance(node, ast.ImportFrom)
            and node.module == "resource"
            for node in tree.body
        )
        measure = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_peak_rss_bytes"
        )
        self.assertFalse(module_imports_resource)
        self.assertTrue(
            any(
                isinstance(node, ast.Import)
                and any(alias.name == "resource" for alias in node.names)
                for node in measure.body
            )
        )

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
            request = _request(api_key_enabled=True)
            user = self.namespace["get_current_user"](
                request,
                BackgroundTasks(self.events),
                Credentials("Bearer", "sk-synthetic"),
            )
        self.assertEqual(user.id, "api-user")
        self.assertIsNone(
            self.namespace["get_validated_session_epoch"](request, user.id)
        )
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
            self.assertEqual((missing.exception.status_code, missing.exception.detail), (404, "User not found."))
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
