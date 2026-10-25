# SPDX-License-Identifier: BSD-3-Clause
"""Exercise the checked-in auth branches without importing app dependencies."""

import ast
import pathlib
import types
import unittest


AUTH_SOURCE = pathlib.Path(__file__).resolve().parents[1] / "utils" / "auth.py"


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Credentials:
    def __init__(self, scheme, credentials):
        self.scheme = scheme
        self.credentials = credentials


class BackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, *args):
        self.calls.append(args)


class UsersStub:
    def __init__(self, users):
        self.users = users
        self.api_key_user = types.SimpleNamespace(id="key-user", role="user")

    def get_user_by_id(self, user_id):
        return self.users.get(user_id)

    def get_user_by_api_key(self, _api_key):
        return self.api_key_user

    def update_user_last_active_by_id(self, user_id):
        return None


def _functions_from_source(namespace):
    tree = ast.parse(AUTH_SOURCE.read_text(encoding="utf-8"), filename=str(AUTH_SOURCE))
    requested = {"get_current_user", "get_verified_user", "get_admin_user"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in requested
    ]
    if {function.name for function in functions} != requested:
        raise AssertionError("could not extract all selected authentication functions")
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(AUTH_SOURCE), "exec"), namespace)
    return namespace


def _parse_http_bearer(header):
    """Model HTTPBearer(auto_error=False)'s absent/non-Bearer input outcome."""
    if not header:
        return None
    scheme, separator, credentials = header.partition(" ")
    if not separator or not scheme or not credentials or scheme.lower() != "bearer":
        return None
    return Credentials(scheme, credentials)


def _request(cookie=None, path="/api/v1/auths/user", enable_api_key=False, restrictions=False):
    return types.SimpleNamespace(
        cookies={} if cookie is None else {"token": cookie},
        state=types.SimpleNamespace(enable_api_key=enable_api_key),
        url=types.SimpleNamespace(path=path),
        app=types.SimpleNamespace(
            state=types.SimpleNamespace(
                config=types.SimpleNamespace(
                    ENABLE_API_KEY_ENDPOINT_RESTRICTIONS=restrictions,
                    API_KEY_ALLOWED_ENDPOINTS="/api/v1/auths",
                )
            )
        ),
    )


def make_auth():
    owner = types.SimpleNamespace(id="owner", role="user")
    member = types.SimpleNamespace(id="member", role="user")
    admin = types.SimpleNamespace(id="admin", role="admin")
    users = UsersStub({"owner": owner, "member": member, "admin": admin})
    messages = types.SimpleNamespace(
        API_KEY_NOT_ALLOWED="API key not allowed",
        INVALID_TOKEN="Invalid token",
        UNAUTHORIZED="Unauthorized",
        ACCESS_PROHIBITED="Access prohibited",
    )
    status = types.SimpleNamespace(
        HTTP_401_UNAUTHORIZED=401,
        HTTP_403_FORBIDDEN=403,
    )
    namespace = _functions_from_source(
        {
            "Request": object,
            "BackgroundTasks": BackgroundTasks,
            "HTTPAuthorizationCredentials": Credentials,
            "Depends": lambda dependency: dependency,
            "HTTPException": HTTPError,
            "bearer_security": object(),
            "Users": users,
            "_session_epochs_enabled": lambda: False,
            "ERROR_MESSAGES": messages,
            "status": status,
            "decode_token": lambda token: {
                "owner-token": {"id": "owner"},
                "member-token": {"id": "member"},
                "admin-token": {"id": "admin"},
                "missing-user-token": {"id": "not-present"},
            }.get(token),
            "get_current_user_by_api_key": lambda token: users.get_user_by_api_key(token),
        }
    )
    return namespace, owner, member, admin, users


class MissingCredentialStatusTests(unittest.TestCase):
    def test_missing_and_empty_bearer_without_cookie_are_401(self):
        auth, *_ = make_auth()
        current_user = auth["get_current_user"]
        for credentials in (None, _parse_http_bearer("Bearer ")):
            with self.subTest(credentials=credentials):
                with self.assertRaises(HTTPError) as caught:
                    current_user(_request(), BackgroundTasks(), credentials)
                self.assertEqual(caught.exception.status_code, 401)
                self.assertEqual(caught.exception.detail, "Not authenticated")

    def test_unrecognized_schemes_fall_back_to_cookie(self):
        auth, owner, *_ = make_auth()
        current_user = auth["get_current_user"]
        for header in ("Basic dXNlcjpwYXNz", "Bearer "):
            with self.subTest(header=header):
                parsed = _parse_http_bearer(header)
                self.assertIsNone(parsed)
                self.assertIs(
                    current_user(
                        _request(cookie="owner-token"), BackgroundTasks(), parsed
                    ),
                    owner,
                )

    def test_recognized_bearer_precedes_cookie_including_invalid_and_member_tokens(self):
        auth, owner, member, *_ = make_auth()
        current_user = auth["get_current_user"]
        with self.assertRaises(HTTPError) as invalid:
            current_user(
                _request(cookie="owner-token"),
                BackgroundTasks(),
                Credentials("Bearer", "invalid-token"),
            )
        self.assertEqual(invalid.exception.status_code, 401)
        self.assertIs(
            current_user(
                _request(cookie="invalid-cookie"),
                BackgroundTasks(),
                Credentials("Bearer", "owner-token"),
            ),
            owner,
        )
        self.assertIs(
            current_user(
                _request(cookie="owner-token"),
                BackgroundTasks(),
                Credentials("Bearer", "member-token"),
            ),
            member,
        )

    def test_invalid_jwt_missing_user_and_role_refusals_keep_401(self):
        auth, *_ = make_auth()
        current_user = auth["get_current_user"]
        for token in ("invalid-token", "missing-user-token"):
            with self.subTest(token=token):
                with self.assertRaises(HTTPError) as caught:
                    current_user(
                        _request(), BackgroundTasks(), Credentials("Bearer", token)
                    )
                self.assertEqual(caught.exception.status_code, 401)

        verified_user = auth["get_verified_user"]
        with self.assertRaises(HTTPError) as denied:
            verified_user(types.SimpleNamespace(id="pending", role="pending"))
        self.assertEqual(denied.exception.status_code, 401)
        admin = types.SimpleNamespace(id="admin", role="admin")
        self.assertIs(auth["get_admin_user"](admin), admin)

    def test_api_key_policy_and_allowed_path_are_unchanged(self):
        auth, *_ = make_auth()
        current_user = auth["get_current_user"]
        key = Credentials("Bearer", "sk-synthetic")
        with self.assertRaises(HTTPError) as disabled:
            current_user(_request(enable_api_key=False), BackgroundTasks(), key)
        self.assertEqual(disabled.exception.status_code, 403)

        with self.assertRaises(HTTPError) as denied_path:
            current_user(
                _request(
                    path="/api/v1/models",
                    enable_api_key=True,
                    restrictions=True,
                ),
                BackgroundTasks(),
                key,
            )
        self.assertEqual(denied_path.exception.status_code, 403)
        allowed = current_user(
            _request(enable_api_key=True, restrictions=True), BackgroundTasks(), key
        )
        self.assertEqual(allowed.id, "key-user")


if __name__ == "__main__":
    unittest.main(verbosity=2)
