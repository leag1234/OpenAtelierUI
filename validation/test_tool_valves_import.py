# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound stored/read/invocation checks for imported Tool valves."""
import ast
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from functools import partial, update_wrapper
import inspect
import logging
import re
from typing import Awaitable, Callable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import migrate_sqlite as importer
from migrate_sqlite import MigrationError
from test_migrate_sqlite import build_source, build_template, digest, marker_tool_source

BACKEND = HERE.parent / "backend" / "open_webui"


def _source_functions(path, names, namespace):
    tree = ast.parse(path.read_text())
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in found} != set(names):
        raise AssertionError("required own-source function was not found")
    exec(compile(ast.Module(body=found, type_ignores=[]), str(path), "exec"), namespace)


def _tool_getters(target):
    model_path = BACKEND / "models" / "tools.py"
    tree = ast.parse(model_path.read_text())
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ToolsTable")
    names = {"get_tool_valves_by_id", "get_user_valves_by_id_and_user_id"}
    methods = [node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {"Optional": Optional, "log": logging.getLogger("test-tool-valves"), "Tool": object}

    class Settings:
        def __init__(self, values): self.values = values
        def model_dump(self): return self.values

    class Users:
        @staticmethod
        def get_user_by_id(user_id):
            db = sqlite3.connect(target)
            try:
                row = db.execute("SELECT settings FROM user WHERE id=?", (user_id,)).fetchone()
                return types.SimpleNamespace(settings=Settings(json.loads(row[0])))
            finally: db.close()

    class DB:
        def get(self, model, identity):
            db = sqlite3.connect(target)
            try:
                row = db.execute("SELECT valves FROM tool WHERE id=?", (identity,)).fetchone()
                return types.SimpleNamespace(valves=json.loads(row[0]) if row[0] is not None else None)
            finally: db.close()

    @contextmanager
    def get_db(): yield DB()

    namespace.update(Users=Users, get_db=get_db)
    namespace["Tool"] = object
    class_node = ast.ClassDef(name="ToolsTable", bases=[], keywords=[], body=methods, decorator_list=[])
    ast.fix_missing_locations(class_node)
    exec(compile(ast.Module(body=[class_node], type_ignores=[]), str(model_path), "exec"), namespace)
    return namespace["ToolsTable"]()


def _actual_get_tools(target, tool, module):
    tools_source = BACKEND / "utils" / "tools.py"
    namespace = {
        "Request": object, "UserModel": object, "Callable": Callable, "Awaitable": Awaitable,
        "inspect": inspect, "partial": partial, "update_wrapper": update_wrapper, "re": re,
        "log": logging.getLogger("test-tool-dispatch"), "load_tool_module_by_id": lambda identity: (module, {}),
    }
    table = _tool_getters(target)
    table.get_tool_by_id = lambda identity: tool if identity == tool.id else None
    namespace["Tools"] = table
    _source_functions(tools_source, {"get_async_tool_function_and_apply_extra_params", "get_tools"}, namespace)
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(TOOLS={tool.id: module})))
    user = types.SimpleNamespace(id="u1", role="user")
    params = {"__user__": {}}
    built = namespace["get_tools"](request, [tool.id], user, params)
    return built, params


class ToolValvesImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.template, self.uploads = self.root / "source.db", self.root / "template.db", self.root / "uploads"
        build_source(self.source, self.uploads)
        build_template(self.template)

    def _configure_tool(self, *, bad_valves=False, unsupported_right=False):
        self.specs = [{"name": "run", "description": "Run a local check", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}}}]
        self.meta = {"description": "Workshop helper", "manifest": {"capability": "text"}, "retained_archive_label": "synthetic"}
        self.valves = {"tone": "concise", "limit": 3}
        self.user_valves = {"nickname": "reader", "count": 2}
        db = sqlite3.connect(self.source)
        db.execute("UPDATE tool SET specs=?,meta=?,valves=? WHERE id='tool-private'", (
            json.dumps(self.specs), json.dumps(self.meta), json.dumps([] if bad_valves else self.valves)))
        db.execute("UPDATE user SET settings=? WHERE id='u1'", (
            json.dumps({"tools": {"valves": {"tool-private": self.user_valves}}}),))
        if unsupported_right:
            db.execute("INSERT INTO access_grant VALUES ('tool-write','tool','tool-private','user','u1','write',14)")
        else:
            db.execute("INSERT INTO access_grant VALUES ('tool-public','tool','tool-private','user','*','read',14)")
        db.commit(); db.close()

    def test_imported_values_are_read_and_used_by_actual_old_tool_dispatch(self):
        self._configure_tool()
        target = self.root / "target"
        report = importer.convert(self.source, self.uploads, self.template, target)
        blockers = {item["code"] for item in report["blockers"]}
        self.assertIn("access_grant_identity_and_time_not_preserved", blockers)
        self.assertIn("tool_metadata_fields_not_behaviorally_preserved", blockers)
        self.assertNotIn("tool_valves_not_behaviorally_preserved", blockers)
        db = sqlite3.connect(target / "webui.db")
        raw = db.execute("SELECT specs,meta,valves,access_control FROM tool WHERE id='tool-private'").fetchone()
        settings = json.loads(db.execute("SELECT settings FROM user WHERE id='u1'").fetchone()[0])
        db.close()
        self.assertEqual(tuple(json.loads(value) if value is not None else None for value in raw[:3]),
                         (self.specs, self.meta, self.valves))
        self.assertIsNone(raw[3])
        self.assertEqual(settings["tools"]["valves"]["tool-private"], self.user_valves)

        class ValveValues:
            def __init__(self, **values): self.values = values
        def run(prompt, __id__, __user__):
            return {"prompt": prompt, "tool_id": __id__, "global": module.valves.values,
                    "per_user": __user__["valves"].values}
        module = types.SimpleNamespace(Valves=ValveValues, UserValves=ValveValues, run=run, valves=None)
        tool = types.SimpleNamespace(id="tool-private", user_id="u1", specs=json.loads(raw[0]), access_control=None)
        dispatch, _ = _actual_get_tools(target / "webui.db", tool, module)
        result = asyncio.run(dispatch["run"]["callable"](prompt="Check the saved settings"))
        self.assertEqual(result, {"prompt": "Check the saved settings", "tool_id": "tool-private",
                                  "global": self.valves, "per_user": self.user_valves})

    def test_malformed_valves_refuse_before_target(self):
        self._configure_tool(bad_valves=True)
        report = importer.preview(self.source, self.uploads, self.template)
        self.assertTrue(report["fatal"])
        self.assertIn("invalid_tool_valves", {item["code"] for item in report["blockers"]})
        with self.assertRaises(MigrationError):
            importer.convert(self.source, self.uploads, self.template, self.root / "bad-valves")
        self.assertFalse((self.root / "bad-valves").exists())

    def test_unsupported_write_right_still_refuses_before_target(self):
        self._configure_tool(unsupported_right=True)
        report = importer.preview(self.source, self.uploads, self.template)
        self.assertTrue(report["fatal"])
        self.assertIn("unsupported_access_grant_semantics", {item["code"] for item in report["blockers"]})
        with self.assertRaises(MigrationError):
            importer.convert(self.source, self.uploads, self.template, self.root / "bad-right")
        self.assertFalse((self.root / "bad-right").exists())


if __name__ == "__main__":
    unittest.main()
