# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused checks for the final v0.11.3 declaration compatibility slice."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "main": ROOT / "backend/open_webui/main.py",
    "functions": ROOT / "backend/open_webui/routers/functions.py",
    "images": ROOT / "backend/open_webui/routers/images.py",
    "models": ROOT / "backend/open_webui/routers/models.py",
    "tasks": ROOT / "backend/open_webui/routers/tasks.py",
    "tools": ROOT / "backend/open_webui/routers/tools.py",
}


def declared_routes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if decorator.func.attr not in {"get", "post"}:
                continue
            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                found.add((decorator.func.attr.upper(), decorator.args[0].value))
    return found


class FinalBaselineRoutesCompatibilityTests(unittest.TestCase):
    def test_all_eight_routes_are_declared(self):
        routes = set()
        for path in FILES.values():
            routes.update(declared_routes(path))
        expected = {
            ("GET", "/cache/{path:path}"),
            ("POST", "/load/url"),
            ("POST", "/sync"),
            ("POST", "/edit"),
            ("POST", "/import"),
            ("POST", "/follow_up/completions"),
        }
        self.assertEqual(set(), expected - routes)
        self.assertEqual(
            2,
            sum(("POST", "/load/url") in declared_routes(path) for path in FILES.values()),
        )
        self.assertEqual(
            2,
            sum(("POST", "/sync") in declared_routes(path) for path in FILES.values()),
        )

    def test_url_loading_is_bounded_and_administrator_only(self):
        helper = (
            ROOT / "backend/open_webui/utils/bounded_url_loader.py"
        ).read_text(encoding="utf-8")
        self.assertIn("MAX_SOURCE_BYTES = 1024 * 1024", helper)
        self.assertIn('parts.scheme not in {"http", "https"}', helper)
        self.assertIn("max_redirects=3", helper)
        for key in ("functions", "tools"):
            source = FILES[key].read_text(encoding="utf-8")
            self.assertIn("Depends(get_admin_user)", source)
            self.assertIn("load_source_url", source)

    def test_cache_route_requires_identity_and_contains_paths(self):
        source = FILES["main"].read_text(encoding="utf-8")
        self.assertIn("user=Depends(get_verified_user)", source)
        self.assertIn("candidate.relative_to(root)", source)
        self.assertIn('content_disposition_type="inline" if inline else "attachment"', source)
        self.assertNotIn('app.mount("/cache"', source)

    def test_disabled_image_edit_is_explicit(self):
        source = FILES["images"].read_text(encoding="utf-8")
        self.assertIn("class EditImageForm(BaseModel)", source)
        self.assertIn("if not request.app.state.config.ENABLE_IMAGE_GENERATION", source)
        self.assertIn("status_code=403", source)


if __name__ == "__main__":
    unittest.main()
