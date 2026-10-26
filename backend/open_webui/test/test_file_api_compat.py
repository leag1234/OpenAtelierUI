# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound checks for the opt-in public file response profile."""

import ast
import asyncio
import os
import pathlib
import types
import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
MODELS_PATH = OPEN_WEBUI / "models" / "files.py"
ROUTES_PATH = OPEN_WEBUI / "routers" / "files.py"


def _extract_classes(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)


def _extract_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)


class FileRecord:
    def __init__(self, *, id="file-01", user_id="owner", access_control=None):
        self.id = id
        self.user_id = user_id
        self.hash = "hash-01"
        self.filename = "note.txt"
        self.path = "/synthetic/note.txt"
        self.data = {"content": "synthetic body"}
        self.meta = {"name": "note.txt", "content_type": "text/plain", "size": 14}
        self.access_control = access_control
        self.created_at = 100
        self.updated_at = 101

    def model_dump(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "hash": self.hash,
            "filename": self.filename,
            "path": self.path,
            "data": self.data,
            "meta": self.meta,
            "access_control": self.access_control,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class HTTPError(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class JSONResponseStub:
    def __init__(self, *, content):
        self.content = content


class Store:
    def __init__(self, files=()):
        self.files = list(files)
        self.lookups = 0
        self.list_calls = []

    def get_files(self):
        self.list_calls.append("all")
        return self.files

    def get_files_by_user_id(self, user_id):
        self.list_calls.append(("user", user_id))
        return [file for file in self.files if file.user_id == user_id]

    def get_file_by_id(self, file_id):
        self.lookups += 1
        return next((file for file in self.files if file.id == file_id), None)


def _load_subjects():
    models = {
        "BaseModel": BaseModel,
        "ConfigDict": ConfigDict,
        "Optional": Optional,
    }
    _extract_classes(
        MODELS_PATH,
        {"FileMeta", "FileModel", "FileModelResponse", "FilePublicMetadataResponse"},
        models,
    )

    store = Store()
    route_namespace = {
        "os": os,
        "Optional": Optional,
        "FileModel": models["FileModel"],
        "FileModelResponse": models["FileModelResponse"],
        "FilePublicMetadataResponse": models["FilePublicMetadataResponse"],
        "Files": store,
        "get_verified_user": lambda: None,
        "Depends": lambda value: value,
        "Query": lambda value: value,
        "JSONResponse": JSONResponseStub,
        "HTTPException": HTTPError,
        "status": types.SimpleNamespace(HTTP_404_NOT_FOUND=404),
        "ERROR_MESSAGES": types.SimpleNamespace(NOT_FOUND="Not found"),
        "has_access_to_file": lambda *_args: False,
    }
    _extract_functions(
        ROUTES_PATH,
        {"current_file_api_enabled", "list_files", "get_file_by_id"},
        route_namespace,
    )
    return models, route_namespace, store


class FileApiCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.previous_flag = os.environ.pop("OPEN_WEBUI_CURRENT_FILE_API", None)
        self.models, self.routes, self.store = _load_subjects()

    def tearDown(self):
        if self.previous_flag is None:
            os.environ.pop("OPEN_WEBUI_CURRENT_FILE_API", None)
        else:
            os.environ["OPEN_WEBUI_CURRENT_FILE_API"] = self.previous_flag

    @staticmethod
    def _set_flag(enabled):
        if enabled:
            os.environ["OPEN_WEBUI_CURRENT_FILE_API"] = "1"
        else:
            os.environ.pop("OPEN_WEBUI_CURRENT_FILE_API", None)

    def test_default_off_list_and_metadata_keep_legacy_shapes(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self.store.files = [row]
        user = types.SimpleNamespace(id="owner", role="user")

        listed = asyncio.run(self.routes["list_files"](user=user))
        self.assertIsInstance(listed, list)
        self.assertEqual(len(listed), 1)
        self.assertIs(listed[0], row)
        self.assertEqual(self.store.list_calls, [("user", "owner")])

        metadata = asyncio.run(self.routes["get_file_by_id"]("file-01", user=user))
        self.assertIs(metadata, row)
        self.assertEqual(metadata.access_control, acl)
        legacy_public = self.models["FileModel"].model_validate(metadata.model_dump())
        self.assertEqual(legacy_public.access_control, acl)

    def test_enabled_list_uses_existing_public_items_and_integer_total(self):
        first = FileRecord(access_control={"read": {"user_ids": ["reader"]}})
        second = FileRecord(id="file-02", access_control=None)
        self.store.files = [first, second]
        self._set_flag(True)
        user = types.SimpleNamespace(id="owner", role="user")

        response = asyncio.run(self.routes["list_files"](user=user))
        self.assertIsInstance(response, JSONResponseStub)
        self.assertEqual(set(response.content), {"items", "total"})
        self.assertEqual(type(response.content["total"]), int)
        self.assertEqual(response.content["total"], 2)
        self.assertEqual([item["id"] for item in response.content["items"]], ["file-01", "file-02"])
        self.assertEqual(response.content["items"][0]["path"], first.path)
        self.assertEqual(response.content["items"][0]["access_control"], first.access_control)
        self.assertEqual(self.store.list_calls, [("user", "owner")])

    def test_enabled_public_metadata_omits_acl_but_keeps_internal_record(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self.store.files = [row]
        self._set_flag(True)
        user = types.SimpleNamespace(id="owner", role="user")

        response = asyncio.run(self.routes["get_file_by_id"]("file-01", user=user))
        self.assertIsInstance(response, JSONResponseStub)
        self.assertEqual(
            set(response.content),
            {"id", "user_id", "hash", "filename", "path", "data", "meta", "created_at", "updated_at"},
        )
        self.assertNotIn("access_control", response.content)
        self.assertEqual(response.content["path"], row.path)
        self.assertEqual(row.access_control, acl)
        self.assertEqual(self.store.lookups, 1)

        internal = self.models["FileModel"].model_validate(row.model_dump())
        self.assertEqual(internal.access_control, acl)

    def test_enabled_mode_keeps_owner_admin_and_existing_denial_and_missing_behavior(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self.store.files = [row]
        self._set_flag(True)

        owner = types.SimpleNamespace(id="owner", role="user")
        response = asyncio.run(self.routes["get_file_by_id"]("file-01", user=owner))
        self.assertEqual(response.content["id"], "file-01")

        admin = types.SimpleNamespace(id="admin", role="admin")
        admin_response = asyncio.run(
            self.routes["get_file_by_id"]("file-01", user=admin)
        )
        self.assertEqual(admin_response.content["id"], "file-01")

        reader_route = self.routes["get_file_by_id"]
        reader_route.__globals__["has_access_to_file"] = lambda *_args: True
        reader = types.SimpleNamespace(id="reader", role="user")
        self.assertEqual(
            asyncio.run(reader_route("file-01", user=reader)).content["id"],
            "file-01",
        )

        reader_route.__globals__["has_access_to_file"] = lambda *_args: False
        unrelated = types.SimpleNamespace(id="unrelated", role="user")
        with self.assertRaises(HTTPError) as denied:
            asyncio.run(reader_route("file-01", user=unrelated))
        self.assertEqual(denied.exception.status_code, 404)
        self.assertEqual(row.access_control, acl)

        self.store.files = []
        with self.assertRaises(HTTPError) as missing:
            asyncio.run(reader_route("missing", user=owner))
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(self.store.lookups, 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
