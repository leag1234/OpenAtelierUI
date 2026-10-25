#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Check loader persistence behavior with isolated synthetic stores and modules."""

from __future__ import annotations

import ast
import os
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "backend/open_webui/utils/plugin.py"


def extract_loaders(namespace):
    tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"), filename=str(PLUGIN_SOURCE))
    requested = {"load_tool_module_by_id", "load_function_module_by_id"}
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in requested
    ]
    found = {node.name for node in functions}
    if found != requested:
        raise AssertionError(f"expected loader functions {requested}, found {found}")
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, str(PLUGIN_SOURCE), "exec"), namespace)
    return namespace


class Store:
    def __init__(self, rows):
        self.rows = rows
        self.get_calls = []
        self.update_calls = []

    def get_tool_by_id(self, identity):
        self.get_calls.append(identity)
        return self.rows.get(identity)

    def update_tool_by_id(self, identity, values):
        self.update_calls.append((identity, dict(values)))
        for key, value in values.items():
            setattr(self.rows[identity], key, value)
        return self.rows[identity]

    def get_function_by_id(self, identity):
        self.get_calls.append(identity)
        return self.rows.get(identity)

    def update_function_by_id(self, identity, values):
        self.update_calls.append((identity, dict(values)))
        for key, value in values.items():
            setattr(self.rows[identity], key, value)
        return self.rows[identity]


class TempFiles:
    def __init__(self):
        self.paths = []

    def NamedTemporaryFile(self, **kwargs):
        result = tempfile.NamedTemporaryFile(**kwargs)
        self.paths.append(result.name)
        return result


class LoaderPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_files = TempFiles()
        self.tools = Store({})
        self.functions = Store({})
        self.namespace = extract_loaders(
            {
                "os": os,
                "sys": sys,
                "types": types,
                "tempfile": self.temp_files,
                "Tools": self.tools,
                "Functions": self.functions,
                "replace_imports": lambda content: content.replace(
                    "from utils", "from open_webui.utils"
                ),
                "extract_frontmatter": lambda content: {},
                "install_frontmatter_requirements": lambda requirements: None,
                "log": types.SimpleNamespace(
                    info=lambda *args, **kwargs: None,
                    error=lambda *args, **kwargs: None,
                ),
            }
        )

    @staticmethod
    def _row(content):
        return types.SimpleNamespace(content=content)

    def test_identical_tool_and_function_loads_do_not_update_storage(self):
        tool_source = "class Tools:\n    marker = 'same'\n"
        function_source = "class Filter:\n    marker = 'same'\n"
        self.tools.rows["same-tool"] = self._row(tool_source)
        self.functions.rows["same-function"] = self._row(function_source)

        tool, _ = self.namespace["load_tool_module_by_id"]("same-tool")
        function, kind, _ = self.namespace["load_function_module_by_id"](
            "same-function"
        )

        self.assertEqual(tool.marker, "same")
        self.assertEqual(function.marker, "same")
        self.assertEqual(kind, "filter")
        self.assertEqual(self.tools.get_calls, ["same-tool"])
        self.assertEqual(self.functions.get_calls, ["same-function"])
        self.assertEqual(self.tools.update_calls, [])
        self.assertEqual(self.functions.update_calls, [])
        self.assertTrue(all(not os.path.exists(path) for path in self.temp_files.paths))

    def test_legacy_import_rewrites_still_persist_once(self):
        tool_source = "# from utils.old import helper\nclass Tools:\n    pass\n"
        function_source = "# from utils.old import helper\nclass Filter:\n    pass\n"
        self.tools.rows["rewrite-tool"] = self._row(tool_source)
        self.functions.rows["rewrite-function"] = self._row(function_source)

        self.namespace["load_tool_module_by_id"]("rewrite-tool")
        self.namespace["load_function_module_by_id"]("rewrite-function")

        rewritten_tool = tool_source.replace("from utils", "from open_webui.utils")
        rewritten_function = function_source.replace("from utils", "from open_webui.utils")
        self.assertEqual(self.tools.update_calls, [("rewrite-tool", {"content": rewritten_tool})])
        self.assertEqual(
            self.functions.update_calls,
            [("rewrite-function", {"content": rewritten_function})],
        )
        self.assertEqual(self.tools.rows["rewrite-tool"].content, rewritten_tool)
        self.assertEqual(self.functions.rows["rewrite-function"].content, rewritten_function)

    def test_explicit_source_skips_database_fetch_and_content_update(self):
        tool_source = "class Tools:\n    marker = 'provided'\n"
        function_source = "class Action:\n    marker = 'provided'\n"

        tool, _ = self.namespace["load_tool_module_by_id"]("external-tool", tool_source)
        function, kind, _ = self.namespace["load_function_module_by_id"](
            "external-function", function_source
        )

        self.assertEqual(tool.marker, "provided")
        self.assertEqual(function.marker, "provided")
        self.assertEqual(kind, "action")
        self.assertEqual(self.tools.get_calls, [])
        self.assertEqual(self.functions.get_calls, [])
        self.assertEqual(self.tools.update_calls, [])
        self.assertEqual(self.functions.update_calls, [])

    def test_failed_function_deactivates_and_cleans_module_and_temp_file(self):
        self.functions.rows["broken-function"] = self._row("raise RuntimeError('synthetic failure')\n")

        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            self.namespace["load_function_module_by_id"]("broken-function")

        self.assertEqual(
            self.functions.update_calls,
            [("broken-function", {"is_active": False})],
        )
        self.assertNotIn("function_broken-function", sys.modules)
        self.assertEqual(len(self.temp_files.paths), 1)
        self.assertFalse(os.path.exists(self.temp_files.paths[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
