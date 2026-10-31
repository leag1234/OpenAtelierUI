#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused controls for the opt-in, bounded provider-error response projection."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "backend/open_webui/utils/provider_errors.py"
SPEC = importlib.util.spec_from_file_location("provider_errors_under_test", HELPER_PATH)
provider_errors = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(provider_errors)


class FragmentReader:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.requested = []

    async def read(self, limit):
        self.requested.append(limit)
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > limit:
            self.chunks.insert(0, chunk[limit:])
            return chunk[:limit]
        return chunk


class ProviderErrorProjectionTests(unittest.TestCase):
    def setUp(self):
        self.old_flag = os.environ.pop("OPENATELIER_PROVIDER_ERROR_COMPAT", None)

    def tearDown(self):
        os.environ.pop("OPENATELIER_PROVIDER_ERROR_COMPAT", None)
        if self.old_flag is not None:
            os.environ["OPENATELIER_PROVIDER_ERROR_COMPAT"] = self.old_flag

    def build(self, status=401, body=None, key="", authorization=None):
        body = body if body is not None else b'{"error":{"message":"Synthetic error","type":"synthetic_protocol_error","code":"synthetic_401"}}'
        return provider_errors.build_provider_error_response(status, body, key, authorization)

    @staticmethod
    def json_body(response):
        return json.loads(response.body.decode("utf-8"))

    def test_feature_flag_is_opt_in_only(self):
        self.assertFalse(provider_errors.provider_error_compat_enabled())
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value):
                os.environ["OPENATELIER_PROVIDER_ERROR_COMPAT"] = value
                self.assertFalse(provider_errors.provider_error_compat_enabled())
        os.environ["OPENATELIER_PROVIDER_ERROR_COMPAT"] = "1"
        self.assertTrue(provider_errors.provider_error_compat_enabled())

    def test_measured_statuses_keep_json_error_envelope_for_plain_and_stream_requests(self):
        body = b'{"error":{"message":"Synthetic provider error","type":"synthetic_protocol_error","code":"synthetic_error"}}'
        for status in (401, 429, 500):
            for stream in (False, True):
                with self.subTest(status=status, stream=stream):
                    response = self.build(status, body)
                    self.assertIsInstance(response, provider_errors.ProviderErrorResponse)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.media_type, "application/json")
                    self.assertEqual(
                        self.json_body(response),
                        {"error": {
                            "message": "Synthetic provider error",
                            "type": "synthetic_protocol_error",
                            "code": "synthetic_error",
                        }},
                    )

    def test_only_measured_fields_are_returned_and_credentials_are_redacted(self):
        body = json.dumps({"error": {
            "message": "bad provider-key and caller-token",
            "type": "protocol",
            "code": "provider-key",
        }}).encode()
        response = self.build(429, body, "provider-key", "Bearer caller-token")
        self.assertEqual(self.json_body(response), {
            "error": {
                "message": "bad [redacted] and [redacted]",
                "type": "protocol",
                "code": "[redacted]",
            }
        })
        self.assertNotIn(b"provider-key", response.body)
        self.assertNotIn(b"caller-token", response.body)

    def test_malformed_oversized_or_unsupported_bodies_use_generic_fallback(self):
        cases = (
            b"not json",
            b"\xff",
            b'{"error":{"message":"\\ud800"}}',
            b'{"error":{"message":"first","message":"second"}}',
            b'{"error":{"message":"bad","extra":"not forwarded"}}',
            b'{"error":{"message":false}}',
            b'{"error":{"message":"NaN","code":NaN}}',
            b'{"message":"wrong envelope"}',
            b'{"error":{"message":"' + (b"x" * (provider_errors.MAX_PROVIDER_ERROR_BYTES + 1)) + b'"}}',
            b'{"error":{"message":"' + (b"x" * (provider_errors.MAX_PROVIDER_ERROR_FIELD_CHARS + 1)) + b'"}}',
        )
        for body in cases:
            with self.subTest(length=len(body)):
                response = self.build(500, body, "secret", "Bearer token")
                self.assertEqual(response.status_code, 500)
                self.assertEqual(self.json_body(response), {
                    "error": {
                        "message": "Provider error response unavailable",
                        "type": "provider_error",
                    }
                })
                self.assertNotIn(b"secret", response.body)
                self.assertNotIn(b"token", response.body)

    def test_status_outside_provider_error_range_uses_safe_gateway_status(self):
        response = self.build(302, b'{"error":{"message":"ignored"}}')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.json_body(response)["error"]["message"], "Provider error response unavailable")

    def test_bounded_body_reader_accumulates_fragments_and_stops_at_oversize_sentinel(self):
        payload = b'{"error":{"message":"fragmented provider error"}}'
        reader = FragmentReader([payload[:3], payload[3:19], payload[19:], b""])
        body = asyncio.run(provider_errors.read_bounded_provider_error_body(reader))
        response = self.build(401, body)
        self.assertEqual(self.json_body(response)["error"]["message"], "fragmented provider error")
        self.assertEqual(reader.chunks, [])
        self.assertGreater(len(reader.requested), 2)

        oversize = FragmentReader([b"x" * provider_errors.MAX_PROVIDER_ERROR_BYTES, b"sentinel"])
        bounded = asyncio.run(provider_errors.read_bounded_provider_error_body(oversize))
        self.assertEqual(len(bounded), provider_errors.MAX_PROVIDER_ERROR_BYTES + 1)
        self.assertEqual(bounded[-1:], b"s")
        self.assertEqual(oversize.chunks, [b"entinel"])
        self.assertTrue(all(0 < count <= provider_errors.MAX_PROVIDER_ERROR_BYTES + 1 for count in oversize.requested))

    def test_outer_gateway_returns_marker_before_success_processing(self):
        source = (ROOT / "backend/open_webui/main.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        handler = next(
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_completion"
        )
        marker_guard = next(
            node for node in ast.walk(handler)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Name)
            and node.test.func.id == "isinstance"
            and len(node.test.args) == 2
            and isinstance(node.test.args[1], ast.Name)
            and node.test.args[1].id == "ProviderErrorResponse"
        )
        self.assertTrue(any(isinstance(node, ast.Return) for node in marker_guard.body))
        process_call = next(
            node for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "process_chat_response"
        )
        self.assertLess(marker_guard.lineno, process_call.lineno)

    def test_provider_route_guards_projection_on_error_status_and_opt_in(self):
        source = (ROOT / "backend/open_webui/routers/openai.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        handler = next(
            node for node in module.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_chat_completion"
        )
        guards = [
            node for node in ast.walk(handler)
            if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "provider_error_compat_enabled"
                for child in ast.walk(node.test)
            )
        ]
        self.assertEqual(len(guards), 1)
        guard = guards[0]
        self.assertTrue(any(isinstance(child, ast.Return) for child in ast.walk(guard)))
        self.assertTrue(any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "build_provider_error_response"
            for child in ast.walk(guard)
        ))
        streaming = next(
            node for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "StreamingResponse"
        )
        self.assertLess(guard.lineno, streaming.lineno)


if __name__ == "__main__":
    unittest.main(verbosity=2)
