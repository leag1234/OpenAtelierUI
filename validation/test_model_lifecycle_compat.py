# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused source checks for measured model lifecycle compatibility routes."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"
OPENAI = ROOT / "backend/open_webui/routers/openai.py"
OLLAMA = ROOT / "backend/open_webui/routers/ollama.py"


def routes(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(
                decorator.func, ast.Attribute
            ):
                continue
            if decorator.func.attr not in {"get", "post", "delete"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            found[(decorator.func.attr.upper(), decorator.args[0].value)] = node
    return found


class ModelLifecycleCompatibilityTests(unittest.TestCase):
    def test_all_measured_routes_are_declared(self):
        declared = {
            **routes(MAIN),
            **routes(OPENAI),
            **routes(OLLAMA),
        }
        expected = {
            ("POST", "/api/models/unload"),
            ("POST", "/api/unload"),
            ("DELETE", "/models/{url_idx}"),
            ("GET", "/models/{url_idx}/catalog"),
            ("GET", "/models/{url_idx}/download/status/{job_id}"),
            ("GET", "/models/{url_idx}/sse"),
            ("POST", "/models/{url_idx}/download"),
            ("POST", "/models/{url_idx}/load"),
            ("POST", "/models/{url_idx}/unload"),
        }
        self.assertEqual(set(), expected - set(declared))

    def test_lifecycle_routes_keep_admin_dependency(self):
        for path in (MAIN, OPENAI, OLLAMA):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in {
                    "unload_model",
                    "delete_provider_model",
                    "get_provider_model_catalog",
                    "get_provider_model_download_status",
                    "stream_provider_model_events",
                    "download_provider_model",
                    "load_provider_model",
                    "unload_provider_model",
                }:
                    continue
                rendered = ast.unparse(node.args)
                self.assertIn("Depends(get_admin_user)", rendered, node.name)

    def test_provider_payload_transform_is_explicit(self):
        source = OPENAI.read_text(encoding="utf-8")
        self.assertIn('{"instance_id": form_data.model}', source)
        self.assertIn('selected.get("provider", "default")', source)
        self.assertIn('detail="Connection not found"', source)

    def test_ollama_unload_is_bounded(self):
        source = OLLAMA.read_text(encoding="utf-8")
        self.assertIn('"keep_alive": 0', source)
        self.assertIn('"prompt": ""', source)
        self.assertIn('detail="Missing name of the model to unload."', source)


if __name__ == "__main__":
    unittest.main()
