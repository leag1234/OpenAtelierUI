#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Extract actual terminal route functions and exercise a fake HTTP session."""

import ast
import asyncio
import importlib.util
import os
import pathlib
import threading
import types
import unittest
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request
from fastapi.responses import Response


ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTER_PATH = ROOT / "backend/open_webui/routers/terminals.py"
POLICY_PATH = ROOT / "backend/open_webui/utils/terminal_download_policy.py"
SPEC = importlib.util.spec_from_file_location("terminal_policy_for_http_test", POLICY_PATH)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)

SYNTHETIC_KEY = "synthetic-terminal-http-key"
BASE_URL = "http://127.0.0.1:18524"
FILE_PATH = "chatcmpl-0123456789abcdef0123456789abcdef/Community%20workshop.pdf"
BODY = b"%PDF-1.4 synthetic attachment\n"


class DummyRouter:
    def get(self, *_args, **_kwargs):
        return lambda function: function

    def api_route(self, *_args, **_kwargs):
        return lambda function: function


class MultiHeaders:
    def __init__(self, values=None):
        self.values = {key: list(value) if isinstance(value, list) else [value]
                       for key, value in (values or {}).items()}

    def getall(self, name, default=None):
        return self.values.get(name, default or [])


class FakeContent:
    def __init__(self, body=b"", total=None):
        self.body = body
        self.total = len(body) if total is None else total
        self.read_calls = 0

    async def iter_chunked(self, size):
        offset = 0
        while offset < self.total:
            amount = min(size, self.total - offset)
            self.read_calls += 1
            if self.body:
                chunk = self.body[offset:offset + amount]
                yield chunk + b"x" * (amount - len(chunk))
            else:
                yield b"x" * amount
            offset += amount


class FakeUpstream:
    def __init__(self, status=200, body=BODY, headers=None, total=None):
        self.status = status
        self.headers = MultiHeaders(headers or {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(body) if total is None else total),
        })
        self.content = FakeContent(body, total)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class FakeSession:
    def __init__(self, harness):
        self.harness = harness

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def request(self, *args, **kwargs):
        self.harness.calls.append((args, kwargs))
        if self.harness.failure:
            raise self.harness.failure
        return FakeResponseContext(self.harness)


class FakeResponseContext:
    def __init__(self, harness):
        self.harness = harness

    async def __aenter__(self):
        self.harness.active += 1
        self.harness.max_active = max(self.harness.max_active, self.harness.active)
        await asyncio.sleep(0.01)
        return self.harness.response

    async def __aexit__(self, *_args):
        self.harness.active -= 1
        return None


class FakeAiohttp:
    class ClientError(Exception):
        pass

    def __init__(self, harness):
        self.harness = harness

    @staticmethod
    def ClientTimeout(**kwargs):
        return kwargs

    def ClientSession(self, **kwargs):
        self.harness.session_options.append(kwargs)
        return FakeSession(self.harness)


class TerminalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.session_options = []
        self.response = FakeUpstream()
        self.failure = None
        self.active = 0
        self.max_active = 0
        self.original_config = os.environ.get("TERMINAL_SERVER_CONNECTIONS")
        namespace = {
            "APIRouter": DummyRouter,
            "Depends": lambda value: value,
            "Request": Request,
            "HTTPException": HTTPException,
            "Response": Response,
            "JSONResponse": object,
            "aiohttp": FakeAiohttp(self),
            "asyncio": asyncio,
            "asynccontextmanager": asynccontextmanager,
            "os": os,
            "threading": threading,
            "quote": __import__("urllib.parse", fromlist=["quote"]).quote,
            "unquote": __import__("urllib.parse", fromlist=["unquote"]).unquote,
            "get_verified_user": lambda: None,
            "TerminalPolicyError": POLICY.TerminalPolicyError,
            "parse_terminal_connections": POLICY.parse_terminal_connections,
            "public_terminal_connections": POLICY.public_terminal_connections,
            "build_download_request": POLICY.build_download_request,
        }
        tree = ast.parse(ROUTER_PATH.read_text(encoding="utf-8"))
        selected = [node for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for node in selected:
            node.decorator_list = []
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTER_PATH), "exec"), namespace)
        namespace["MAX_DOWNLOAD_BYTES"] = 16 * 1024 * 1024
        namespace["DOWNLOAD_CHUNK_BYTES"] = 64 * 1024
        namespace["_FETCH_LIMIT"] = threading.BoundedSemaphore(4)
        namespace["_fetch_slot"] = asynccontextmanager(namespace["_fetch_slot"])
        self.ns = namespace
        self.user = types.SimpleNamespace(id="owner-1", role="user")
        self.config = [{
            "id": "atlas-files",
            "url": BASE_URL,
            "key": SYNTHETIC_KEY,
            "auth_type": "bearer",
            "enabled": True,
            "name": "",
            "config": {"chat_uploads": "default", "access_grants": [
                {"principal_type": "user", "principal_id": "owner-1", "permission": "read"}
            ]},
        }]
        os.environ["TERMINAL_SERVER_CONNECTIONS"] = __import__("json").dumps(self.config)

    def tearDown(self):
        if self.original_config is None:
            os.environ.pop("TERMINAL_SERVER_CONNECTIONS", None)
        else:
            os.environ["TERMINAL_SERVER_CONNECTIONS"] = self.original_config

    def request(self, method="GET", raw_path=None, query="", headers=None):
        return types.SimpleNamespace(
            method=method,
            url=types.SimpleNamespace(query=query),
            scope={"raw_path": raw_path or ("/api/v1/terminals/atlas-files/files/serve/home/user/requests/" + FILE_PATH).encode()},
            headers=headers or {"Authorization": "caller-secret", "X-User-Id": "forged-user"},
        )

    def invoke_download(self, method="GET", path=FILE_PATH, request=None, user=None):
        return asyncio.run(self.ns["download_terminal_file"](
            "atlas-files", path, request or self.request(method), user or self.user
        ))

    def test_exact_bytes_safe_headers_and_no_caller_header_forwarding(self):
        result = self.invoke_download()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, BODY)
        self.assertEqual(result.headers["content-type"], "application/pdf")
        self.assertEqual(result.headers["content-disposition"],
                         "attachment; filename=\"download\"; filename*=UTF-8''Community%20workshop.pdf")
        self.assertEqual(result.headers["x-content-type-options"], "nosniff")
        self.assertEqual(result.headers["content-security-policy"], "sandbox")
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(result.headers["content-length"], str(len(BODY)))
        args, kwargs = self.calls[0]
        self.assertEqual(args, ("GET", BASE_URL + "/files/serve/home/user/requests/" + FILE_PATH))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer " + SYNTHETIC_KEY,
                                             "X-User-Id": "owner-1", "Accept-Encoding": "identity"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(set(kwargs), {"headers", "allow_redirects"})
        self.assertEqual(self.session_options[0]["trust_env"], False)
        self.assertEqual(self.session_options[0]["auto_decompress"], False)
        self.assertLessEqual(self.session_options[0]["timeout"]["total"], 15)
        self.assertLessEqual(self.session_options[0]["timeout"]["connect"], 5)

    def test_head_does_not_read_or_return_body(self):
        self.response.content.total = 0
        result = self.invoke_download("HEAD")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.body, b"")
        self.assertEqual(self.calls[0][0][0], "HEAD")
        self.assertEqual(self.response.content.read_calls, 0)

    def test_fetch_concurrency_is_bounded(self):
        spec = POLICY.build_download_request(
            __import__("json").dumps(self.config), "atlas-files",
            "files/serve/home/user/requests/" + FILE_PATH, self.user
        )
        async def fetch_many():
            return await asyncio.gather(*[
                self.ns["_fetch_file"](self.request(), spec, "Community workshop.pdf")
                for _ in range(6)
            ])
        results = asyncio.run(fetch_many())
        self.assertEqual(len(results), 6)
        self.assertEqual(self.max_active, 4)
        self.assertEqual(self.active, 0)

    def test_main_registration_is_opt_in_and_precedes_spa_fallback(self):
        main = (ROOT / "backend/open_webui/main.py").read_text(encoding="utf-8")
        guard = 'os.environ.get("OPENATELIER_TERMINAL_DOWNLOAD_COMPAT") == "1"'
        self.assertEqual(main.count(guard), 2)
        self.assertLess(main.index("app.include_router(terminals.router"), main.index('app.mount(\n        "/"'))

    def test_list_route_returns_only_authorized_public_projection(self):
        result = asyncio.run(self.ns["list_terminal_connections"](self.user))
        self.assertEqual(result, [{
            "id": "atlas-files", "name": "", "url": BASE_URL, "contexts": {},
            "config": {"chat_uploads": "default"},
        }])
        self.assertNotIn(SYNTHETIC_KEY, repr(result))
        self.assertNotIn("owner-1", repr(result))
        denied = asyncio.run(self.ns["list_terminal_connections"](
            types.SimpleNamespace(id="other", role="user")
        ))
        self.assertEqual(denied, [])

    def test_active_content_type_and_upstream_headers_are_not_forwarded(self):
        self.response = FakeUpstream(headers={
            "Content-Type": "text/html", "Content-Length": str(len(BODY)),
            "Set-Cookie": "session=private", "Location": SYNTHETIC_KEY,
        })
        result = self.invoke_download()
        self.assertEqual(result.headers["content-type"], "application/octet-stream")
        self.assertNotIn("set-cookie", result.headers)
        self.assertNotIn("location", result.headers)

    def test_unrequested_content_encoding_is_refused_before_body_read(self):
        self.response = FakeUpstream(headers={
            "Content-Type": "application/pdf", "Content-Encoding": "gzip",
        })
        with self.assertRaises(HTTPException) as caught:
            self.invoke_download()
        self.assertEqual(caught.exception.status_code, 502)
        self.assertEqual(self.response.content.read_calls, 0)

    def test_invalid_raw_path_query_or_user_never_calls_upstream(self):
        for request, path, user in (
            (self.request(raw_path=b"/api/v1/terminals/id/%2fforged"), FILE_PATH, self.user),
            (self.request(query="download=1"), FILE_PATH, self.user),
            (self.request(), FILE_PATH, types.SimpleNamespace(id="other", role="user")),
        ):
            with self.subTest(request=request):
                with self.assertRaises(HTTPException) as caught:
                    self.invoke_download(path=path, request=request, user=user)
                self.assertIn(caught.exception.status_code, (403,))
        self.assertEqual(self.calls, [])

    def test_unsupported_paths_methods_and_malformed_config_are_explicit(self):
        with self.assertRaises(HTTPException) as get_error:
            asyncio.run(self.ns["refuse_terminal_operation"]("other", self.request(), self.user))
        self.assertEqual(get_error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as post_error:
            asyncio.run(self.ns["refuse_terminal_operation"](
                "file", self.request("POST"), self.user
            ))
        self.assertEqual(post_error.exception.status_code, 405)
        os.environ["TERMINAL_SERVER_CONNECTIONS"] = "not-json"
        with self.assertRaises(HTTPException) as config_error:
            self.invoke_download()
        self.assertEqual(config_error.exception.status_code, 503)
        self.assertEqual(self.calls, [])

    def test_upstream_statuses_redirects_and_transport_errors_are_sanitized(self):
        cases = [(302, 502), (500, 502), (401, 401), (403, 403), (404, 404)]
        for status, expected in cases:
            self.response = FakeUpstream(status=status, headers={"Location": SYNTHETIC_KEY})
            with self.subTest(status=status):
                with self.assertRaises(HTTPException) as caught:
                    self.invoke_download()
                self.assertEqual(caught.exception.status_code, expected)
                self.assertNotIn(SYNTHETIC_KEY, str(caught.exception.detail))
        self.response = FakeUpstream()
        self.failure = asyncio.TimeoutError()
        with self.assertRaises(HTTPException) as timeout:
            self.invoke_download()
        self.assertEqual(timeout.exception.status_code, 504)
        self.failure = FakeAiohttp.ClientError("private transport detail")
        with self.assertRaises(HTTPException) as transport:
            self.invoke_download()
        self.assertEqual(transport.exception.status_code, 503)
        self.assertNotIn("private transport", str(transport.exception.detail))

    def test_length_and_streaming_caps_stop_oversized_responses(self):
        self.response = FakeUpstream(headers={"Content-Length": str(16 * 1024 * 1024 + 1)})
        with self.assertRaises(HTTPException) as declared:
            self.invoke_download()
        self.assertEqual(declared.exception.status_code, 413)
        self.assertEqual(declared.exception.detail, "Terminal file exceeds the download limit.")
        self.assertEqual(self.response.content.read_calls, 0)
        self.response = FakeUpstream(body=b"", headers={"Content-Type": "application/pdf"}, total=16 * 1024 * 1024)
        exact = self.invoke_download()
        self.assertEqual(len(exact.body), 16 * 1024 * 1024)
        self.response = FakeUpstream(headers={"Content-Type": "application/pdf"}, total=16 * 1024 * 1024 + 1)
        with self.assertRaises(HTTPException) as streamed:
            self.invoke_download()
        self.assertEqual(streamed.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
