#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Named tests for the opt-in inert imported-history prototype."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import time
from pathlib import Path
from types import SimpleNamespace

from migrate_sqlite import convert
from test_migrate_sqlite import build_source, build_template

_HELPER_PATH = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "utils" / "imported_history.py"
_HELPER_SPEC = importlib.util.spec_from_file_location("_imported_history_test_module", _HELPER_PATH)
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
import sys
sys.modules[_HELPER_SPEC.name] = _HELPER
_HELPER_SPEC.loader.exec_module(_HELPER)
HistoryNotFound = _HELPER.HistoryNotFound
HistoryUnavailable = _HELPER.HistoryUnavailable
get_history = _HELPER.get_history
get_current_version_id = _HELPER.get_current_version_id
list_history = _HELPER.list_history


def add_history(source: Path, kind: str, resource_id: str, count: int, *, author_id: str = "u1") -> list[str]:
    owner_column = "tool_id" if kind == "tool" else "model_id"
    snapshot = ({
        "name": "Synthetic history tool",
        "content": "# inert historical source\ndef pipe(**kwargs): return 'old'\n",
        "meta": {"description": "synthetic", "manifest": {}},
    } if kind == "tool" else {
        "name": "Synthetic history model", "base_model_id": "portable_pipe", "params": {}, "meta": {},
    })
    db = sqlite3.connect(source)
    ids = ["tool-revision-%03d" % i if kind == "tool" else "model-revision-%03d" % i for i in range(count)]
    db.execute(f'UPDATE "{kind}" SET version_id=? WHERE id=?', (ids[-1], resource_id))
    for index, revision_id in enumerate(ids):
        parent_id = ids[index - 1] if index else None
        db.execute(
            f'INSERT INTO "{kind}_history" VALUES (?,?,?,?,?,?,?)',
            (revision_id, resource_id, parent_id, json.dumps(snapshot, separators=(",", ":")), author_id,
             "Synthetic revision %03d" % index, 100),
        )
    db.commit()
    db.close()
    return ids


class ImportedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="imported-history-")
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
        self.tool_ids = add_history(self.source, "tool", "tool-private", 21)
        self.model_ids = add_history(self.source, "model", "model-private", 2, author_id="u2")
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        report = convert(self.source, self.uploads, self.template, self.target, include_history_sidecar=True)
        self.assertTrue(report["imported_history_sidecar"]["opt_in"])
        self.assertFalse(report["cutover_eligible"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)
        self.data_dir = self.target
        self.database_url = "sqlite:///" + str(self.target / "webui.db")
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"

    def tearDown(self):
        os.environ.pop("OPEN_WEBUI_IMPORTED_HISTORY_READS", None)
        self.temp.cleanup()

    @staticmethod
    def user_lookup(user_id):
        names = {"u1": "Synthetic User", "u2": "Synthetic administrator"}
        return SimpleNamespace(name=names[user_id]) if user_id in names else None

    def resource(self, kind):
        return SimpleNamespace(id="tool-private" if kind == "tool" else "model-private", user_id="u1")

    def test_lists_are_bounded_ordered_and_omit_snapshots(self):
        page_one = list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)
        page_two = list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 2, self.user_lookup)
        self.assertEqual(len(page_one), 20)
        self.assertEqual([row["id"] for row in page_one], list(reversed(self.tool_ids[1:])))
        self.assertEqual([row["id"] for row in page_two], self.tool_ids[:1])
        self.assertTrue(all("snapshot" not in row for row in page_one))
        self.assertEqual(page_one[0]["user"], {"name": "Synthetic User"})
        self.assertEqual(list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 0, self.user_lookup), page_one)
        self.assertEqual(list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 10**100, self.user_lookup), [])

    def test_detail_preserves_inert_snapshot_and_rejects_cross_resource_ids(self):
        detail = get_history(self.data_dir, self.database_url, "tool", self.resource("tool"), self.tool_ids[-1], self.user_lookup)
        self.assertEqual(detail["snapshot"]["content"], "# inert historical source\ndef pipe(**kwargs): return 'old'\n")
        self.assertEqual(detail["user"], {"name": "Synthetic User"})
        with self.assertRaises(HistoryNotFound):
            get_history(self.data_dir, self.database_url, "model", self.resource("model"), self.tool_ids[-1], self.user_lookup)

    def test_current_version_metadata_matches_verified_imported_pointer_without_snapshot(self):
        self.assertEqual(
            get_current_version_id(self.data_dir, self.database_url, "tool", self.resource("tool")),
            self.tool_ids[-1],
        )
        self.assertEqual(
            get_current_version_id(self.data_dir, self.database_url, "model", self.resource("model")),
            self.model_ids[-1],
        )
        os.environ.pop("OPEN_WEBUI_IMPORTED_HISTORY_READS", None)
        with self.assertRaises(HistoryUnavailable):
            get_current_version_id(self.data_dir, self.database_url, "tool", self.resource("tool"))
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"

    def test_corrupt_current_pointer_fails_helper_validation(self):
        sidecar = sqlite3.connect(self.target / "imported-history.sqlite")
        sidecar.execute(
            "UPDATE history_manifest SET current_version_id='missing-version' WHERE kind='tool' AND resource_id='tool-private'"
        )
        sidecar.commit()
        sidecar.close()
        with self.assertRaises(HistoryUnavailable):
            get_current_version_id(self.data_dir, self.database_url, "tool", self.resource("tool"))

    def test_admin_authored_history_is_not_treated_as_resource_authority(self):
        detail = get_history(self.data_dir, self.database_url, "model", self.resource("model"), self.model_ids[-1], self.user_lookup)
        self.assertEqual(detail["user_id"], "u2")
        self.assertEqual(detail["user"], {"name": "Synthetic administrator"})

    def test_disabled_missing_corrupt_and_stale_sidecars_fail_closed(self):
        os.environ.pop("OPEN_WEBUI_IMPORTED_HISTORY_READS", None)
        with self.assertRaises(HistoryUnavailable):
            list_history(self.root / "absent", self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)
        os.environ["OPEN_WEBUI_IMPORTED_HISTORY_READS"] = "1"
        sidecar = self.target / "imported-history.sqlite"
        original = sidecar.read_bytes()
        sidecar.unlink()
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)
        sidecar.write_bytes(original)
        sidecar.write_bytes(b"not sqlite")
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)
        sidecar.write_bytes(original)
        db = sqlite3.connect(self.target / "webui.db")
        db.execute("UPDATE tool SET content=? WHERE id=?", ("edited without a history version", "tool-private"))
        db.commit()
        db.close()
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)

    def test_owner_change_is_stale_even_for_the_new_live_owner(self):
        db = sqlite3.connect(self.target / "webui.db")
        db.execute("UPDATE tool SET user_id='u2' WHERE id='tool-private'")
        db.commit()
        db.close()
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, self.database_url, "tool", SimpleNamespace(id="tool-private", user_id="u2"), 1, self.user_lookup)

    def test_default_conversion_does_not_emit_history_sidecar(self):
        plain_target = self.root / "plain-target"
        report = convert(self.source, self.uploads, self.template, plain_target)
        self.assertNotIn("imported_history_sidecar", report)
        self.assertFalse((plain_target / "imported-history.sqlite").exists())

    def test_runtime_switch_refuses_custom_database_path(self):
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, "sqlite:////tmp/other.db", "tool", self.resource("tool"), 1, self.user_lookup)

    def test_open_failure_is_unavailable_not_server_error(self):
        from unittest.mock import patch
        with patch.object(_HELPER, "_connect", side_effect=PermissionError("synthetic unreadable database")):
            with self.assertRaises(HistoryUnavailable):
                list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)

    def test_live_database_lock_is_respected_and_times_out(self):
        lock_script = (
            "import sqlite3,sys,time; c=sqlite3.connect(sys.argv[1],timeout=1); "
            "c.execute('BEGIN EXCLUSIVE'); print('locked',flush=True); time.sleep(5)"
        )
        writer = subprocess.Popen(
            [sys.executable, "-c", lock_script, str(self.target / "webui.db")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        started = time.monotonic()
        try:
            self.assertEqual(writer.stdout.readline().strip(), "locked")
            with self.assertRaises(HistoryUnavailable):
                list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)
            self.assertGreaterEqual(time.monotonic() - started, 0.8)
        finally:
            writer.terminate()
            writer.wait(timeout=2)
            writer.stdout.close()
            writer.stderr.close()

    def test_snapshot_must_remain_json_object_and_oversized_sidecar_refuses(self):
        sidecar = sqlite3.connect(self.target / "imported-history.sqlite")
        sidecar.execute("UPDATE tool_history SET snapshot='[]' WHERE id=?", (self.tool_ids[-1],))
        sidecar.commit()
        sidecar.close()
        with self.assertRaises(HistoryUnavailable):
            get_history(self.data_dir, self.database_url, "tool", self.resource("tool"), self.tool_ids[-1], self.user_lookup)
        path = self.target / "imported-history.sqlite"
        with path.open("ab") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaises(HistoryUnavailable):
            list_history(self.data_dir, self.database_url, "tool", self.resource("tool"), 1, self.user_lookup)

    def _extract_route(self, filename, function_name, resource, user, helper_calls, *, missing=False, helper_error=None):
        base = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "routers"
        tree = ast.parse((base / filename).read_text(encoding="utf-8"))
        node = next(item for item in tree.body if isinstance(item, ast.AsyncFunctionDef) and item.name == function_name)
        node.decorator_list = []

        class RouteHTTPException(Exception):
            def __init__(self, status_code, detail=None):
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        def reader(*args):
            helper_calls.append(args)
            if helper_error:
                raise helper_error
            return ["history"]

        namespace = {
            "Depends": lambda _dependency: None,
            "get_verified_user": lambda: None,
            "HTTPException": RouteHTTPException,
            "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_403_FORBIDDEN=403),
            "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="access prohibited", NOT_FOUND="not found"),
            "Tools": SimpleNamespace(get_tool_by_id=lambda _id: None if missing else resource),
            "Models": SimpleNamespace(get_model_by_id=lambda _id: None if missing else resource),
            "Users": SimpleNamespace(get_user_by_id=self.user_lookup),
            "DATA_DIR": self.data_dir,
            "DATABASE_URL": self.database_url,
            "HistoryNotFound": HistoryNotFound,
            "HistoryUnavailable": HistoryUnavailable,
            "list_imported_tool_history": reader,
            "get_imported_tool_history": reader,
            "list_imported_model_history": reader,
            "get_imported_model_history": reader,
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(base / filename), "exec"), namespace)
        route = namespace[function_name]
        if function_name.endswith("_version"):
            return asyncio.run(route(resource.id, "version-1", user=user))
        if "model" in function_name:
            return asyncio.run(route(resource.id, user=user))
        return asyncio.run(route(resource.id, user=user))

    def test_extracted_routes_enforce_access_before_reader_and_match_missing_errors(self):
        for filename, kind, id_field, list_name, detail_name, expected_denial in (
            ("tools.py", "tool", "tool-private", "get_tool_history", "get_tool_history_version", 401),
            ("models.py", "model", "model-private", "get_model_history", "get_model_history_version", 403),
        ):
            resource = SimpleNamespace(id=id_field, user_id="u1")
            for function_name in (list_name, detail_name):
                for user in (SimpleNamespace(id="u1", role="user"), SimpleNamespace(id="admin", role="admin")):
                    calls = []
                    self.assertEqual(self._extract_route(filename, function_name, resource, user, calls), ["history"])
                    self.assertEqual(len(calls), 1)
                calls = []
                with self.assertRaises(Exception) as denied:
                    self._extract_route(filename, function_name, resource, SimpleNamespace(id="other", role="user"), calls)
                self.assertEqual(denied.exception.status_code, expected_denial)
                self.assertEqual(calls, [])

                calls = []
                with self.assertRaises(Exception) as missing:
                    self._extract_route(filename, function_name, resource, SimpleNamespace(id="u1", role="user"), calls, missing=True)
                self.assertEqual(missing.exception.status_code, 404)
                self.assertEqual(missing.exception.detail, "Not found" if kind == "tool" else "We could not find what you're looking for :/")
                self.assertEqual(calls, [])

                calls = []
                with self.assertRaises(Exception) as unavailable:
                    self._extract_route(filename, function_name, resource, SimpleNamespace(id="u1", role="user"), calls, helper_error=HistoryUnavailable("unavailable"))
                self.assertEqual(unavailable.exception.status_code, 503)
                self.assertEqual(calls and len(calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
