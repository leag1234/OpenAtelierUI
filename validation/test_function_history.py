#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for the opt-in inert imported function-history slice."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from migrate_sqlite import MigrationError, convert, preview
from test_migrate_sqlite import build_source, build_template

HELPER_PATH = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "utils" / "imported_history.py"
HELPER_SPEC = importlib.util.spec_from_file_location("_function_history_test_helper", HELPER_PATH)
HELPER = importlib.util.module_from_spec(HELPER_SPEC)
import sys
sys.modules[HELPER_SPEC.name] = HELPER
HELPER_SPEC.loader.exec_module(HELPER)


def add_function_history(source: Path, count: int = 23) -> list[tuple]:
    db = sqlite3.connect(source)
    db.execute("DROP TABLE function_history")
    db.execute("""CREATE TABLE function_history(
        id TEXT, function_id TEXT, parent_id TEXT, snapshot TEXT,
        user_id TEXT, commit_message TEXT, created_at INTEGER
    )""")
    db.execute(
        'UPDATE "function" SET version_id=?, is_active=0, meta=? WHERE id=?',
        (f"function-revision-{count:03d}", json.dumps({"description": "Synthetic function", "manifest": {}, "toggle": False}), "fn1"),
    )
    expected = []
    for index in range(1, count + 1):
        revision_id = f"function-revision-{index:03d}"
        parent_id = f"function-revision-{index - 1:03d}" if index > 1 else None
        snapshot = {
            "name": "Synthetic function",
            "content": f"# inert historical revision {index}\ndef outlet(body): return body\n",
            "meta": {"description": "Synthetic history", "manifest": {"revision": index}, "toggle": False},
        }
        snapshot_text = json.dumps(snapshot, separators=(",", ":"))
        created_at = 100 if index in (18, count) else 100 + index
        author_id = "u2" if index == 17 else "u1"
        row = (revision_id, "fn1", parent_id, snapshot_text, author_id, None if index == 1 else f"Synthetic revision {index}", created_at)
        expected.append(row)
        db.execute("INSERT INTO function_history VALUES (?,?,?,?,?,?,?)", row)
    db.commit()
    db.close()
    return expected


class FunctionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="function-history-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source.sqlite"
        self.uploads = self.root / "uploads"
        self.template = self.root / "template.sqlite"
        self.target = self.root / "target"
        build_source(self.source, self.uploads)
        build_template(self.template)
        db = sqlite3.connect(self.source)
        db.execute("INSERT INTO auth VALUES ('u2','admin@example.invalid','bcrypt-placeholder',1,NULL,NULL)")
        db.execute("INSERT INTO user VALUES ('u2','Synthetic administrator','admin@example.invalid','admin','', 't','t','t','{}','{}',NULL,NULL,'{}','{}','{}')")
        db.commit()
        db.close()
        self.expected = add_function_history(self.source)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        os.environ.pop("OPEN_WEBUI_IMPORTED_HISTORY_READS", None)
        self.temp.cleanup()

    @staticmethod
    def user_lookup(user_id):
        names = {"u1": "Synthetic User", "u2": "Synthetic administrator"}
        return SimpleNamespace(name=names[user_id]) if user_id in names else None

    @staticmethod
    def resource():
        return SimpleNamespace(id="fn1", user_id="u1")

    def prepare(self):
        report = convert(
            self.source, self.uploads, self.template, self.target,
            include_function_history=True,
        )
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"
        return report

    def test_default_remains_archive_only_and_opt_in_preserves_rows_inertly(self):
        default = preview(self.source, self.uploads, self.template)
        self.assertIn("unsupported_nonempty_domain_function_history", {item["code"] for item in default["blockers"]})
        self.assertNotIn("function_history", default)

        report = self.prepare()
        self.assertFalse(report["cutover_eligible"])
        self.assertEqual(report["function_history"]["revision_count"], 23)
        self.assertEqual(report["imported_history_sidecar"]["runtime_switch"], "OPEN_WEBUI_IMPORTED_HISTORY_READS=1")
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        sidecar = sqlite3.connect(self.target / "imported-history.sqlite")
        self.assertEqual(
            sidecar.execute('SELECT id,function_id,parent_id,snapshot,user_id,commit_message,created_at FROM function_history ORDER BY id').fetchall(),
            sorted(self.expected),
        )
        manifest = sidecar.execute(
            "SELECT kind,resource_id,owner_id,current_version_id,row_count FROM history_manifest WHERE kind='function'"
        ).fetchone()
        sidecar.close()
        self.assertEqual(manifest, ("function", "fn1", "u1", "function-revision-023", 23))
        self.assertFalse((self.root / "tool-executed-marker").exists())

    def test_real_cli_flag_previews_and_prepares_only_the_opted_in_sidecar(self):
        script = Path(__file__).resolve().parent / "migrate_sqlite.py"
        common = [
            sys.executable, str(script), "--source-db", str(self.source),
            "--uploads-root", str(self.uploads), "--empty-old-target-template", str(self.template),
        ]
        preview_process = subprocess.run(
            common[:2] + ["preview"] + common[2:] + ["--include-function-history"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(preview_process.returncode, 3)
        self.assertEqual(json.loads(preview_process.stdout)["function_history"]["revision_count"], 23)
        cli_target = self.root / "cli-target"
        convert_process = subprocess.run(
            common[:2] + ["convert"] + common[2:] + ["--new-target-dir", str(cli_target), "--include-function-history"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(convert_process.returncode, 3)
        payload = json.loads(convert_process.stdout)
        self.assertTrue(payload["imported_history_sidecar"]["function_history_enabled"])
        self.assertTrue((cli_target / "imported-history.sqlite").is_file())
        connection = sqlite3.connect(cli_target / "imported-history.sqlite")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM function_history").fetchone()[0], 23)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tool_history").fetchone()[0], 0)
        connection.close()
        combined_target = self.root / "combined-target"
        combined = convert(
            self.source, self.uploads, self.template, combined_target,
            include_history_sidecar=True, include_function_history=True,
        )
        self.assertFalse(combined["cutover_eligible"])
        combined_db = sqlite3.connect(combined_target / "imported-history.sqlite")
        self.assertEqual(combined_db.execute("SELECT COUNT(*) FROM function_history").fetchone()[0], 23)
        self.assertEqual(combined_db.execute("SELECT COUNT(*) FROM tool_history").fetchone()[0], 0)
        self.assertEqual(combined_db.execute("SELECT COUNT(*) FROM model_history").fetchone()[0], 0)
        combined_db.close()

    def test_function_list_detail_pointer_and_toggle_are_bounded_and_stale_safe(self):
        report = self.prepare()
        data_dir = self.target
        database_url = "sqlite:///" + str(data_dir / "webui.db")
        first = HELPER.list_history(data_dir, database_url, "function", self.resource(), 1, self.user_lookup)
        second = HELPER.list_history(data_dir, database_url, "function", self.resource(), 2, self.user_lookup)
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 3)
        expected_order = [row[0] for row in sorted(self.expected, key=lambda item: (item[6], item[0]), reverse=True)]
        self.assertEqual([row["id"] for row in first], expected_order[:20])
        self.assertEqual(set(first[0]), {"id", "function_id", "parent_id", "user_id", "commit_message", "created_at", "user"})
        self.assertNotIn("snapshot", first[0])
        self.assertEqual(first[0]["user"], {"name": "Synthetic User"})
        detail = HELPER.get_history(data_dir, database_url, "function", self.resource(), "function-revision-023", self.user_lookup)
        self.assertEqual(detail["snapshot"]["name"], "Synthetic function")
        self.assertEqual(detail["parent_id"], "function-revision-022")
        admin_authored = HELPER.get_history(data_dir, database_url, "function", self.resource(), "function-revision-017", self.user_lookup)
        self.assertEqual(admin_authored["user_id"], "u2")
        self.assertEqual(admin_authored["user"], {"name": "Synthetic administrator"})
        oldest = HELPER.get_history(data_dir, database_url, "function", self.resource(), "function-revision-001", self.user_lookup)
        self.assertIsNone(oldest["parent_id"])
        self.assertIsNone(oldest["commit_message"])
        self.assertEqual(
            HELPER.list_history(data_dir, database_url, "function", self.resource(), 0, self.user_lookup),
            first,
        )
        with self.assertRaises(HELPER.HistoryNotFound):
            HELPER.get_history(data_dir, database_url, "function", self.resource(), "absent", self.user_lookup)
        self.assertEqual(
            HELPER.get_current_function_metadata(data_dir, database_url, self.resource()),
            {"version_id": "function-revision-023", "toggle": False},
        )
        from fastapi.routing import serialize_response
        serialized_page = asyncio.run(serialize_response(field=None, response_content=first))
        serialized_detail = asyncio.run(serialize_response(field=None, response_content=detail))
        self.assertIsInstance(serialized_page, list)
        self.assertEqual(serialized_page[0].keys(), first[0].keys())
        self.assertEqual(serialized_detail["snapshot"], detail["snapshot"])
        os.environ.pop("OPEN_WEBUI_IMPORTED_HISTORY_READS", None)
        with self.assertRaises(HELPER.HistoryUnavailable):
            HELPER.list_history(data_dir, database_url, "function", self.resource(), 1, self.user_lookup)
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"
        db = sqlite3.connect(data_dir / "webui.db")
        db.execute('UPDATE "function" SET content=? WHERE id=?', ("changed", "fn1"))
        db.commit()
        db.close()
        with self.assertRaises(HELPER.HistoryUnavailable):
            HELPER.list_history(data_dir, database_url, "function", self.resource(), 1, self.user_lookup)
        self.assertTrue(report["imported_history_sidecar"]["history_is_functionally_cut_over"] is False)

    def test_invalid_parent_author_pointer_snapshot_and_schema_refuse_before_target(self):
        mutations = {
            "unknown_author": "UPDATE function_history SET user_id='missing' WHERE id='function-revision-001'",
            "unknown_function": "UPDATE function_history SET function_id='missing' WHERE id='function-revision-001'",
            "broken_parent": "UPDATE function_history SET parent_id='missing' WHERE id='function-revision-002'",
            "cycle": "UPDATE function_history SET parent_id='function-revision-002' WHERE id='function-revision-001'",
            "bad_pointer": "UPDATE \"function\" SET version_id='missing' WHERE id='fn1'",
            "bad_snapshot": "UPDATE function_history SET snapshot='[]' WHERE id='function-revision-001'",
            "duplicate_snapshot_key": "UPDATE function_history SET snapshot='{\"name\":\"a\",\"name\":\"b\",\"content\":\"x\",\"meta\":{}}' WHERE id='function-revision-001'",
            "nonfinite_snapshot": "UPDATE function_history SET snapshot='{\"name\":\"a\",\"content\":\"x\",\"meta\":{\"value\":Infinity}}' WHERE id='function-revision-001'",
            "surrogate_snapshot": "UPDATE function_history SET snapshot='{\"name\":\"\\ud800\",\"content\":\"x\",\"meta\":{}}' WHERE id='function-revision-001'",
            "oversized_snapshot": "UPDATE function_history SET snapshot=? WHERE id='function-revision-001'",
            "duplicate_id": "UPDATE function_history SET id='function-revision-001' WHERE id='function-revision-002'",
            "unknown_schema": "ALTER TABLE function_history ADD COLUMN extra TEXT",
            "missing_table": "DROP TABLE function_history",
        }
        for name, statement in mutations.items():
            with self.subTest(name=name):
                candidate = self.root / (name + ".sqlite")
                candidate.write_bytes(self.source.read_bytes())
                db = sqlite3.connect(candidate)
                if name == "oversized_snapshot":
                    db.execute(statement, (json.dumps({"name": "x", "content": "x" * (256 * 1024), "meta": {}}),))
                else:
                    db.execute(statement)
                db.commit()
                db.close()
                target = self.root / (name + "-target")
                before_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
                with self.assertRaises(MigrationError) as refused:
                    convert(candidate, self.uploads, self.template, target, include_function_history=True)
                self.assertEqual(refused.exception.code, "fatal_source_input")
                self.assertFalse(target.exists())
                self.assertEqual(hashlib.sha256(candidate.read_bytes()).hexdigest(), before_hash)

    def test_cross_function_parent_refuses_before_target(self):
        candidate = self.root / "cross-parent.sqlite"
        candidate.write_bytes(self.source.read_bytes())
        db = sqlite3.connect(candidate)
        db.execute(
            'INSERT INTO "function" VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            ("fn2", "u1", "Synthetic second", "filter", "active", '{"toggle":false}', "t", "t", "{}", 0, 0, "foreign-revision"),
        )
        snapshot = json.dumps({"name": "Synthetic second", "content": "old", "meta": {"toggle": False}})
        db.execute(
            "INSERT INTO function_history VALUES (?,?,?,?,?,?,?)",
            ("foreign-revision", "fn2", None, snapshot, "u1", None, 100),
        )
        db.execute(
            "UPDATE function_history SET parent_id='foreign-revision' WHERE id='function-revision-002'"
        )
        db.commit()
        db.close()
        target = self.root / "cross-parent-target"
        with self.assertRaises(MigrationError) as refused:
            convert(candidate, self.uploads, self.template, target, include_function_history=True)
        self.assertEqual(refused.exception.code, "fatal_source_input")
        self.assertFalse(target.exists())

    def test_corrupt_or_missing_function_sidecar_is_never_reported_as_empty(self):
        self.prepare()
        sidecar = self.target / "imported-history.sqlite"
        connection = sqlite3.connect(sidecar)
        connection.execute("UPDATE function_history SET snapshot='[]' WHERE id='function-revision-023'")
        connection.commit()
        connection.close()
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"
        with self.assertRaises(HELPER.HistoryUnavailable):
            HELPER.get_history(self.target, "sqlite:///" + str(self.target / "webui.db"), "function", self.resource(), "function-revision-023", self.user_lookup)
        sidecar.unlink()
        with self.assertRaises(HELPER.HistoryUnavailable):
            HELPER.list_history(self.target, "sqlite:///" + str(self.target / "webui.db"), "function", self.resource(), 1, self.user_lookup)

    def test_route_handlers_authorize_before_lookup_or_sidecar_access(self):
        router_path = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "routers" / "functions.py"
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
        calls = []
        state = {"helper_error": None, "missing_function": False}

        class RouteError(Exception):
            def __init__(self, status_code, detail=None):
                super().__init__(detail)
                self.status_code = status_code

        def reader(*args):
            calls.append("sidecar")
            if state["helper_error"] is not None:
                raise state["helper_error"]
            return ["synthetic history"]

        function_reads = []
        namespace = {
            "Depends": lambda dependency: None,
            "Query": lambda **kwargs: kwargs.get("default"),
            "get_admin_user": lambda: None,
            "HTTPException": RouteError,
            "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="access prohibited"),
            "Functions": SimpleNamespace(get_function_by_id=lambda identity: function_reads.append(identity) or (None if state["missing_function"] else self.resource())),
            "Users": SimpleNamespace(get_user_by_id=self.user_lookup),
            "DATA_DIR": self.target,
            "DATABASE_URL": "sqlite:///" + str(self.target / "webui.db"),
            "HistoryNotFound": HELPER.HistoryNotFound,
            "HistoryUnavailable": HELPER.HistoryUnavailable,
            "list_imported_function_history": reader,
            "get_imported_function_history": reader,
        }
        for function_name in ("get_function_history", "get_function_history_version"):
            calls.clear()
            function_reads.clear()
            node = next(item for item in tree.body if isinstance(item, ast.AsyncFunctionDef) and item.name == function_name)
            node.decorator_list = []
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(router_path), "exec"), namespace)
            endpoint = namespace[function_name]
            with self.assertRaises(RouteError) as denied:
                if function_name.endswith("version"):
                    import asyncio
                    asyncio.run(endpoint("fn1", "revision", user=SimpleNamespace(role="user")))
                else:
                    import asyncio
                    asyncio.run(endpoint("fn1", user=SimpleNamespace(role="user")))
            self.assertEqual(denied.exception.status_code, 401)
            self.assertEqual(calls, [])
            self.assertEqual(function_reads, [])
            import asyncio
            if function_name.endswith("version"):
                result = asyncio.run(endpoint("fn1", "revision", user=SimpleNamespace(role="admin")))
            else:
                result = asyncio.run(endpoint("fn1", page=2, user=SimpleNamespace(role="admin")))
            self.assertEqual(result, ["synthetic history"])
            self.assertEqual(calls, ["sidecar"])
            self.assertEqual(function_reads, ["fn1"])
            calls.clear()
            function_reads.clear()
            state["helper_error"] = HELPER.HistoryUnavailable("unavailable")
            with self.assertRaises(RouteError) as unavailable:
                if function_name.endswith("version"):
                    asyncio.run(endpoint("fn1", "revision", user=SimpleNamespace(role="admin")))
                else:
                    asyncio.run(endpoint("fn1", page=1, user=SimpleNamespace(role="admin")))
            self.assertEqual(unavailable.exception.status_code, 503)
            self.assertEqual(calls, ["sidecar"])
            state["helper_error"] = HELPER.HistoryNotFound()
            calls.clear()
            with self.assertRaises(RouteError) as absent_revision:
                if function_name.endswith("version"):
                    asyncio.run(endpoint("fn1", "missing", user=SimpleNamespace(role="admin")))
                else:
                    asyncio.run(endpoint("fn1", page=1, user=SimpleNamespace(role="admin")))
            self.assertEqual(absent_revision.exception.status_code, 404)
            state["helper_error"] = None
            state["missing_function"] = True
            calls.clear()
            with self.assertRaises(RouteError) as absent_function:
                if function_name.endswith("version"):
                    asyncio.run(endpoint("fn1", "revision", user=SimpleNamespace(role="admin")))
                else:
                    asyncio.run(endpoint("fn1", page=1, user=SimpleNamespace(role="admin")))
            self.assertEqual(absent_function.exception.status_code, 404)
            self.assertEqual(calls, [])
            state["missing_function"] = False

    def test_opt_in_active_detail_adds_only_measured_pointer_metadata(self):
        router_path = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "routers" / "functions.py"
        tree = ast.parse(router_path.read_text(encoding="utf-8"))
        node = next(item for item in tree.body if isinstance(item, ast.AsyncFunctionDef) and item.name == "get_function_by_id")
        node.decorator_list = []
        active = SimpleNamespace(
            model_dump=lambda: {
                "id": "fn1", "user_id": "u1", "name": "Synthetic function",
                "type": "filter", "content": "active content", "meta": {"description": "Synthetic", "manifest": {}},
                "is_active": False, "is_global": False, "updated_at": 1, "created_at": 1,
            }
        )

        class RouteError(Exception):
            def __init__(self, status_code, detail=None):
                super().__init__(detail)
                self.status_code = status_code

        namespace = {
            "_FUNCTION_HISTORY_READS": True,
            "Depends": lambda dependency: None,
            "get_admin_user": lambda: None,
            "Functions": SimpleNamespace(get_function_by_id=lambda _identity: active),
            "get_current_function_metadata": lambda *_args: {"version_id": "function-revision-023", "toggle": False},
            "DATA_DIR": self.target,
            "DATABASE_URL": "sqlite:///" + str(self.target / "webui.db"),
            "HistoryUnavailable": HELPER.HistoryUnavailable,
            "HTTPException": RouteError,
            "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(NOT_FOUND="not found"),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(router_path), "exec"), namespace)
        response = asyncio.run(namespace["get_function_by_id"]("fn1", user=SimpleNamespace(role="admin")))
        self.assertEqual(response["version_id"], "function-revision-023")
        self.assertIs(response["meta"]["toggle"], False)
        self.assertEqual(response["content"], "active content")
        from fastapi.routing import serialize_response
        serialized = asyncio.run(serialize_response(field=None, response_content=response))
        self.assertEqual(serialized["version_id"], "function-revision-023")
        self.assertIs(serialized["meta"]["toggle"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
