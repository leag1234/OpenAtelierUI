# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the measured count, status and rename file-route contract."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "backend/open_webui/routers/files.py"
MODELS = ROOT / "backend/open_webui/models/files.py"


class HTTPError(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class StreamResponse:
    def __init__(self, content, media_type):
        self.content = content
        self.media_type = media_type


class ImmediateAsyncio:
    @staticmethod
    async def sleep(_seconds):
        return None


class FileRecord:
    def __init__(
        self,
        identifier="file-01",
        owner="owner",
        *,
        data=None,
        file_hash=None,
        filename="source.txt",
    ):
        self.id = identifier
        self.user_id = owner
        self.data = {} if data is None else data
        self.hash = file_hash
        self.filename = filename
        self.meta = {"name": filename, "content_type": "text/plain", "size": 12}
        self.path = f"/synthetic/{identifier}_{filename}"
        self.access_control = None
        self.created_at = 100
        self.updated_at = 101

    def model_dump(self, exclude_none=False, exclude=None):
        values = dict(vars(self))
        if exclude_none:
            values = {key: value for key, value in values.items() if value is not None}
        for key in exclude or set():
            values.pop(key, None)
        return values


class Store:
    def __init__(self, files=()):
        self.files = {file.id: file for file in files}
        self.count_calls = []
        self.rename_calls = []
        self.lookup_sequence = []

    def count_files(self, user_id=None):
        self.count_calls.append(user_id)
        return (
            sum(file.user_id == user_id for file in self.files.values())
            if user_id
            else len(self.files)
        )

    def get_file_by_id(self, identifier):
        if self.lookup_sequence:
            return self.lookup_sequence.pop(0)
        return self.files.get(identifier)

    def rename_file_by_id(self, identifier, filename):
        self.rename_calls.append((identifier, filename))
        file = self.files.get(identifier)
        if file:
            file.filename = filename
            file.meta = {**file.meta, "name": filename}
        return file


def load_subjects(store):
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"), filename=str(ROUTES))
    selected = []
    names = {
        "FileRenameForm",
        "_file_process_status",
        "count_files",
        "get_file_process_status",
        "rename_file_by_id",
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == names
    namespace = {
        "BaseModel": BaseModel,
        "FileModel": FileRecord,
        "Files": store,
        "Depends": lambda _value: None,
        "Query": lambda default, **_kwargs: default,
        "get_verified_user": lambda: None,
        "HTTPException": HTTPError,
        "status": NS(HTTP_404_NOT_FOUND=404),
        "ERROR_MESSAGES": NS(NOT_FOUND="Not found"),
        "StreamingResponse": StreamResponse,
        "asyncio": ImmediateAsyncio,
        "json": json,
        "has_access_to_file": lambda *_args: False,
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ROUTES), "exec"),
        namespace,
    )
    return namespace


class FileRouteContractTests(unittest.TestCase):
    def setUp(self):
        self.owner_file = FileRecord()
        self.foreign_file = FileRecord("file-02", "foreign")
        self.store = Store((self.owner_file, self.foreign_file))
        self.subject = load_subjects(self.store)
        self.owner = NS(id="owner", role="user")
        self.foreign = NS(id="foreign", role="user")
        self.admin = NS(id="admin", role="admin")

    def test_count_is_owner_scoped_and_admin_counts_every_file(self):
        self.assertEqual(asyncio.run(self.subject["count_files"](user=self.owner)), 1)
        self.assertEqual(asyncio.run(self.subject["count_files"](user=self.admin)), 2)
        self.assertEqual(self.store.count_calls, ["owner", None])

    def test_status_uses_explicit_state_then_processed_fallback(self):
        status_of = self.subject["_file_process_status"]
        self.assertEqual(status_of(FileRecord(data={"status": "pending"})), "pending")
        self.assertEqual(
            status_of(FileRecord(data={"status": "completed"})), "completed"
        )
        self.assertEqual(status_of(FileRecord(data={}, file_hash=None)), "pending")
        self.assertEqual(
            status_of(FileRecord(data={"content": ""}, file_hash=None)), "completed"
        )
        self.assertEqual(
            status_of(FileRecord(data={}, file_hash="sha256")), "completed"
        )

    def test_status_denies_missing_and_foreign_as_not_found(self):
        route = self.subject["get_file_process_status"]
        for identifier, user in (("missing", self.owner), ("file-01", self.foreign)):
            with self.assertRaises(HTTPError) as caught:
                asyncio.run(route(identifier, False, user=user))
            self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(
            asyncio.run(route("file-01", False, user=self.owner)),
            {"status": "pending"},
        )
        self.assertEqual(
            asyncio.run(route("file-01", False, user=self.admin)),
            {"status": "pending"},
        )

    def test_stream_emits_only_state_transitions_and_stops_at_completed(self):
        pending = FileRecord(data={"status": "pending"})
        completed = FileRecord(data={"status": "completed"})
        self.store.lookup_sequence = [pending, pending, completed]
        response = asyncio.run(
            self.subject["get_file_process_status"]("file-01", True, user=self.owner)
        )
        self.assertEqual(response.media_type, "text/event-stream")

        async def collect():
            return [item async for item in response.content]

        self.assertEqual(
            asyncio.run(collect()),
            [
                'data: {"status": "pending"}\n\n',
                'data: {"status": "completed"}\n\n',
            ],
        )

    def test_rename_owner_and_admin_update_name_without_changing_path(self):
        route = self.subject["rename_file_by_id"]
        form = self.subject["FileRenameForm"](filename="renamed.txt")
        original_path = self.owner_file.path
        owner_result = asyncio.run(route("file-01", form, user=self.owner))
        self.assertEqual(owner_result["filename"], "renamed.txt")
        self.assertEqual(owner_result["meta"]["name"], "renamed.txt")
        self.assertEqual(owner_result["path"], original_path)

        admin_form = self.subject["FileRenameForm"](filename="admin-renamed.txt")
        admin_result = asyncio.run(route("file-01", admin_form, user=self.admin))
        self.assertEqual(admin_result["filename"], "admin-renamed.txt")
        self.assertEqual(
            self.store.rename_calls,
            [("file-01", "renamed.txt"), ("file-01", "admin-renamed.txt")],
        )

    def test_rename_foreign_and_missing_have_no_write_effect(self):
        route = self.subject["rename_file_by_id"]
        form = self.subject["FileRenameForm"](filename="must-not-change.txt")
        for identifier in ("file-01", "missing"):
            with self.assertRaises(HTTPError) as caught:
                asyncio.run(route(identifier, form, user=self.foreign))
            self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.store.rename_calls, [])
        self.assertEqual(self.owner_file.filename, "source.txt")

    def test_persistence_helpers_filter_count_and_commit_atomic_rename(self):
        tree = ast.parse(MODELS.read_text(encoding="utf-8"), filename=str(MODELS))
        table = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "FilesTable"
        )
        methods = {
            node.name: ast.unparse(node)
            for node in table.body
            if isinstance(node, ast.FunctionDef)
        }
        count_source = methods["count_files"]
        rename_source = methods["rename_file_by_id"]
        self.assertIn("File.user_id == user_id", count_source)
        self.assertIn("query.count()", count_source)
        self.assertIn("file.filename = filename", rename_source)
        self.assertIn("'name': filename", rename_source)
        self.assertIn("file.updated_at = int(time.time())", rename_source)
        self.assertIn("db.commit()", rename_source)
        self.assertIn("db.refresh(file)", rename_source)

    def test_upload_declares_background_processing_and_reference_status_metadata(self):
        source = ROUTES.read_text(encoding="utf-8")
        self.assertIn("background_tasks: BackgroundTasks", source)
        self.assertIn('"file_hash": hashlib.sha256(contents).hexdigest()', source)
        self.assertIn('Files.update_file_data_by_id(id, {"status": "pending"})', source)
        self.assertIn("background_tasks.add_task(", source)
        self.assertIn(
            'Files.update_file_data_by_id(file_id, {"status": "completed"})', source
        )
        self.assertIn(
            'Files.update_file_data_by_id(file_id, {"status": "failed"})', source
        )


if __name__ == "__main__":
    unittest.main()
