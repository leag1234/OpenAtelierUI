#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Dependency-free tests for uploaded-HTML response policy and route boundaries."""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "backend/open_webui/utils/uploaded_html.py"
ROUTER_PATH = ROOT / "backend/open_webui/routers/files.py"
spec = importlib.util.spec_from_file_location("uploaded_html_policy", POLICY_PATH)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
router_tree = ast.parse(ROUTER_PATH.read_text(encoding="utf-8"))
functions = {node.name: node for node in router_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def call_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


class UploadedHTMLPolicyTests(unittest.TestCase):
    def test_csp_is_exact_and_static_style_remains_allowed(self):
        headers = policy.uploaded_html_response_headers()
        self.assertEqual(
            headers["Content-Security-Policy"],
            "sandbox; default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
        )
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_route_policy_survives_global_security_headers_middleware(self):
        route_headers = policy.uploaded_html_response_headers()
        configured = {
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "no-referrer",
        }
        update = policy.configured_security_headers_for_response(route_headers, configured)
        self.assertNotIn("Content-Security-Policy", update)
        self.assertEqual(update["Referrer-Policy"], "no-referrer")
        route_headers.update(update)
        self.assertEqual(route_headers["Content-Security-Policy"], policy.UPLOADED_HTML_CONTENT_SECURITY_POLICY)
        no_route_policy = policy.configured_security_headers_for_response({}, configured)
        self.assertEqual(no_route_policy["Content-Security-Policy"], "default-src 'self'")

    def test_global_middleware_merges_through_route_policy_helper(self):
        middleware = ast.parse((ROOT / "backend/open_webui/utils/security_headers.py").read_text(encoding="utf-8"))
        dispatch = next(node for node in ast.walk(middleware) if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch")
        self.assertIn("configured_security_headers_for_response", ast.unparse(dispatch))

    def test_dedicated_html_route_keeps_auth_and_serves_unmodified_file_response(self):
        route = functions["get_html_file_content_by_id"]
        source = ast.unparse(route)
        decorator_path = ast.literal_eval(route.decorator_list[0].args[0])
        self.assertEqual(decorator_path, "/{id}/content/html")
        self.assertIn("Depends(get_verified_user)", source)
        self.assertIn("file.user_id == user.id", source)
        self.assertIn("user.role == 'admin'", source)
        self.assertIn("has_access_to_file(id, 'read', user)", source)
        self.assertIn("FileResponse(file_path", source)
        self.assertIn("headers=uploaded_html_response_headers()", source)
        self.assertNotIn("media_type=", source)

    def test_generic_routes_remain_download_or_safe_text_pdf_behavior(self):
        # The source has two same-named handlers. Locate all route bodies by decorator path.
        routes = [node for node in router_tree.body if isinstance(node, ast.AsyncFunctionDef)]
        body_by_path = {}
        for route in routes:
            for decorator in route.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "get":
                    path = ast.literal_eval(decorator.args[0]) if decorator.args and isinstance(decorator.args[0], ast.Constant) else ""
                    body_by_path[path] = ast.unparse(route)
        content = body_by_path["/{id}/content"]
        named = body_by_path["/{id}/content/{file_name}"]
        self.assertIn('attachment: bool=Query(False)', content)
        self.assertIn("'Content-Disposition'", content)
        self.assertIn("'X-Content-Type-Options': 'nosniff'", content)
        self.assertIn("'application/pdf'", content)
        self.assertIn('attachment; filename*=', content)
        self.assertIn('attachment; filename*=', named)
        self.assertIn("'X-Content-Type-Options': 'nosniff'", named)
        self.assertIn("StreamingResponse", named)
        self.assertNotIn("uploaded_html_response_headers", content + named)


if __name__ == "__main__":
    unittest.main(verbosity=2)
