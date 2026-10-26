# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound checks for the opt-in public file response profile."""

import ast
import asyncio
import os
import pathlib
import types
import unittest
from typing import Optional

from pydantic import BaseModel, ConfigDict, ValidationError


OPEN_WEBUI = pathlib.Path(__file__).resolve().parents[1]
MODELS_PATH = OPEN_WEBUI / "models" / "files.py"
ROUTES_PATH = OPEN_WEBUI / "routers" / "files.py"


def _extract_classes(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)


def _extract_functions(path, names, namespace, *, preserve_decorators=False):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names:
            if not preserve_decorators:
                node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in names:
            if not preserve_decorators:
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
        {
            "FileMeta", "FileModel", "FileModelResponse", "FileListResponse",
            "FilePublicMetadataResponse",
        },
        models,
    )

    store = Store()
    route_namespace = {
        "os": os,
        "Optional": Optional,
        "FileModel": models["FileModel"],
        "FileModelResponse": models["FileModelResponse"],
        "FileListResponse": models["FileListResponse"],
        "FilePublicMetadataResponse": models["FilePublicMetadataResponse"],
        "CURRENT_FILE_API_ENABLED": False,
        "Files": store,
        "get_verified_user": lambda: None,
        "Depends": lambda value: value,
        "Query": lambda value: value,
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
    route_namespace["CURRENT_FILE_API_ENABLED"] = route_namespace[
        "current_file_api_enabled"
    ]()
    return models, route_namespace, store


def _registered_file_response_models(enabled, models):
    class RouteRegistry:
        def __init__(self):
            self.responses = {}

        def get(self, path, *, response_model):
            def register(function):
                self.responses[path] = response_model
                return function

            return register

    router = RouteRegistry()
    namespace = {
        **models,
        "router": router,
        "CURRENT_FILE_API_ENABLED": enabled,
        "Depends": lambda value: value,
        "Query": lambda value: value,
        "get_verified_user": lambda: None,
    }
    _extract_functions(
        ROUTES_PATH,
        {"list_files", "get_file_by_id"},
        namespace,
        preserve_decorators=True,
    )
    return router.responses


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

    def _refresh_route_flag(self):
        self.routes["CURRENT_FILE_API_ENABLED"] = self.routes[
            "current_file_api_enabled"
        ]()

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
        self._refresh_route_flag()
        user = types.SimpleNamespace(id="owner", role="user")

        response = asyncio.run(self.routes["list_files"](user=user))
        self.assertIsInstance(response, self.models["FileListResponse"])
        self.assertEqual(type(response.total), int)
        self.assertEqual(response.total, 2)
        self.assertEqual([item.id for item in response.items], ["file-01", "file-02"])
        self.assertEqual(
            set(response.items[0].model_dump()),
            set(self.models["FileModelResponse"].model_fields),
        )
        self.assertNotIn("path", response.items[0].model_dump())
        self.assertNotIn("access_control", response.items[0].model_dump())
        self.assertEqual(first.path, "/synthetic/note.txt")
        self.assertEqual(first.access_control, {"read": {"user_ids": ["reader"]}})
        self.assertEqual(self.store.list_calls, [("user", "owner")])

    def test_enabled_public_metadata_omits_acl_but_keeps_internal_record(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self.store.files = [row]
        self._set_flag(True)
        self._refresh_route_flag()
        user = types.SimpleNamespace(id="owner", role="user")

        response = asyncio.run(self.routes["get_file_by_id"]("file-01", user=user))
        self.assertIsInstance(response, self.models["FilePublicMetadataResponse"])
        response_data = response.model_dump()
        self.assertEqual(
            set(response_data),
            {"id", "user_id", "hash", "filename", "path", "data", "meta", "created_at", "updated_at"},
        )
        self.assertEqual(response_data["path"], row.path)
        self.assertEqual(row.access_control, acl)
        self.assertEqual(self.store.lookups, 1)

        public_model = self.models["FilePublicMetadataResponse"]
        self.assertTrue(public_model.model_fields["created_at"].is_required())
        self.assertTrue(public_model.model_fields["updated_at"].is_required())
        incomplete = dict(row.model_dump())
        incomplete.pop("created_at")
        with self.assertRaises(ValidationError):
            public_model.model_validate(incomplete)

        internal = self.models["FileModel"].model_validate(row.model_dump())
        self.assertEqual(internal.access_control, acl)

    def test_enabled_mode_keeps_owner_admin_and_existing_denial_and_missing_behavior(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self.store.files = [row]
        self._set_flag(True)
        self._refresh_route_flag()

        owner = types.SimpleNamespace(id="owner", role="user")
        response = asyncio.run(self.routes["get_file_by_id"]("file-01", user=owner))
        self.assertEqual(response.id, "file-01")

        admin = types.SimpleNamespace(id="admin", role="admin")
        admin_response = asyncio.run(
            self.routes["get_file_by_id"]("file-01", user=admin)
        )
        self.assertEqual(admin_response.id, "file-01")

        reader_route = self.routes["get_file_by_id"]
        reader_route.__globals__["has_access_to_file"] = lambda *_args: True
        reader = types.SimpleNamespace(id="reader", role="user")
        self.assertEqual(
            asyncio.run(reader_route("file-01", user=reader)).id,
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

    def test_startup_flag_selects_matching_fastapi_response_models(self):
        legacy = _registered_file_response_models(False, self.models)
        self.assertEqual(legacy["/"], list[self.models["FileModelResponse"]])
        self.assertEqual(legacy["/{id}"], Optional[self.models["FileModel"]])

        enabled = _registered_file_response_models(True, self.models)
        self.assertIs(enabled["/"], self.models["FileListResponse"])
        self.assertIs(enabled["/{id}"], self.models["FilePublicMetadataResponse"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
