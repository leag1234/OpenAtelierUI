# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound checks for the opt-in public file response profile."""

import ast
import asyncio
import contextlib
import inspect
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
        self.page_calls = []

    def get_files(self):
        self.list_calls.append("all")
        return self.files

    def get_files_by_user_id(self, user_id):
        self.list_calls.append(("user", user_id))
        return [file for file in self.files if file.user_id == user_id]

    def get_files_page(self, user_id, page):
        self.page_calls.append((user_id, page))
        selected = [file for file in self.files if user_id is None or file.user_id == user_id]
        selected.sort(key=lambda file: (file.updated_at, file.id), reverse=True)
        start = (page - 1) * 50
        return len(selected), selected[start : start + 50]

    def get_file_by_id(self, file_id):
        self.lookups += 1
        return next((file for file in self.files if file.id == file_id), None)


class QuerySpec:
    def __init__(self, default, **kwargs):
        self.default = default
        self.kwargs = kwargs


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
    class RouteRegistry:
        def __init__(self):
            self.responses = {}
            self.handlers = {}

        def get(self, path, *, response_model):
            def register(function):
                self.responses[path] = response_model
                self.handlers[path] = function
                return function

            return register

    router = RouteRegistry()
    route_namespace = {
        "os": os,
        "Optional": Optional,
        "FileModel": models["FileModel"],
        "FileModelResponse": models["FileModelResponse"],
        "FileListResponse": models["FileListResponse"],
        "FilePublicMetadataResponse": models["FilePublicMetadataResponse"],
        "router": router,
        "CURRENT_FILE_API_ENABLED": False,
        "Files": store,
        "get_verified_user": lambda: None,
        "Depends": lambda value: value,
        "Query": lambda value, **kwargs: QuerySpec(value, **kwargs),
        "HTTPException": HTTPError,
        "status": types.SimpleNamespace(HTTP_404_NOT_FOUND=404),
        "ERROR_MESSAGES": types.SimpleNamespace(NOT_FOUND="Not found"),
        "has_access_to_file": lambda *_args: False,
    }
    tree = ast.parse(ROUTES_PATH.read_text(encoding="utf-8"), filename=str(ROUTES_PATH))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "current_file_api_enabled":
            node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CURRENT_FILE_API_ENABLED"
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "CURRENT_FILE_API_ENABLED":
            selected.append(node)
        elif isinstance(node, ast.AsyncFunctionDef) and node.name == "get_file_by_id":
            selected.append(node)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ROUTES_PATH), "exec"), route_namespace)
    return models, route_namespace, store


def _registered_file_response_models(enabled, models):
    previous = os.environ.get("OPEN_WEBUI_CURRENT_FILE_API")
    if enabled:
        os.environ["OPEN_WEBUI_CURRENT_FILE_API"] = "1"
    else:
        os.environ.pop("OPEN_WEBUI_CURRENT_FILE_API", None)
    try:
        return _load_subjects()[1]["router"].responses
    finally:
        if previous is None:
            os.environ.pop("OPEN_WEBUI_CURRENT_FILE_API", None)
        else:
            os.environ["OPEN_WEBUI_CURRENT_FILE_API"] = previous


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
        self.models, self.routes, self.store = _load_subjects()

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
        self._set_flag(True)
        self._refresh_route_flag()
        self.store.files = [first, second]
        user = types.SimpleNamespace(id="owner", role="user")

        response = asyncio.run(self.routes["list_files"](user=user, page=1))
        self.assertIsInstance(response, self.models["FileListResponse"])
        self.assertEqual(type(response.total), int)
        self.assertEqual(response.total, 2)
        self.assertEqual([item.id for item in response.items], ["file-02", "file-01"])
        self.assertEqual(
            set(response.items[0].model_dump()),
            set(self.models["FileModelResponse"].model_fields),
        )
        self.assertNotIn("path", response.items[0].model_dump())
        self.assertNotIn("access_control", response.items[0].model_dump())
        self.assertEqual(first.path, "/synthetic/note.txt")
        self.assertEqual(first.access_control, {"read": {"user_ids": ["reader"]}})
        self.assertEqual(self.store.page_calls, [("owner", 1)])

    def test_enabled_public_metadata_omits_acl_but_keeps_internal_record(self):
        acl = {"read": {"user_ids": ["reader"]}}
        row = FileRecord(access_control=acl)
        self._set_flag(True)
        self._refresh_route_flag()
        self.store.files = [row]
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
        self._set_flag(True)
        self._refresh_route_flag()
        self.store.files = [row]

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
        legacy = self.routes["router"].responses
        self.assertEqual(legacy["/"], list[self.models["FileModelResponse"]])
        self.assertEqual(legacy["/{id}"], Optional[self.models["FileModel"]])
        self.assertNotIn("page", inspect.signature(self.routes["list_files"]).parameters)

        self._set_flag(True)
        self._refresh_route_flag()
        enabled = self.routes["router"].responses
        self.assertIs(enabled["/"], self.models["FileListResponse"])
        self.assertIs(enabled["/{id}"], self.models["FilePublicMetadataResponse"])
        params = inspect.signature(self.routes["list_files"]).parameters
        self.assertEqual(params["page"].default.default, 1)
        self.assertEqual(params["page"].default.kwargs, {"ge": 1})

    def test_current_page_filters_sorts_and_returns_only_bounded_pages(self):
        own = [FileRecord(id=f"own-{index:02}", user_id="owner") for index in range(55)]
        other = [FileRecord(id=f"other-{index:02}", user_id="other") for index in range(3)]
        for index, row in enumerate(own + other):
            row.updated_at = index
        self._set_flag(True)
        self._refresh_route_flag()
        self.store.files = own + other
        user = types.SimpleNamespace(id="owner", role="user")

        first = asyncio.run(self.routes["list_files"](user=user, page=1))
        second = asyncio.run(self.routes["list_files"](user=user, page=2))
        empty = asyncio.run(self.routes["list_files"](user=user, page=3))
        self.assertEqual((first.total, len(first.items)), (55, 50))
        self.assertEqual((second.total, len(second.items)), (55, 5))
        self.assertEqual((empty.total, len(empty.items)), (55, 0))
        self.assertEqual(first.items[0].id, "own-54")
        self.assertEqual(second.items[-1].id, "own-00")
        self.assertEqual(self.store.page_calls, [("owner", 1), ("owner", 2), ("owner", 3)])

        admin = types.SimpleNamespace(id="admin", role="admin")
        admin_page = asyncio.run(self.routes["list_files"](user=admin, page=1))
        self.assertEqual((admin_page.total, len(admin_page.items)), (58, 50))
        self.assertEqual(self.store.page_calls[-1], (None, 1))

    def test_content_false_does_not_mutate_file_data(self):
        self._set_flag(True)
        self._refresh_route_flag()
        row = FileRecord()
        self.store.files = [row]
        user = types.SimpleNamespace(id="owner", role="user")
        response = asyncio.run(self.routes["list_files"](user=user, page=1, content=False))
        self.assertEqual(response.items[0].data, {})
        self.assertEqual(row.data, {"content": "synthetic body"})

    def test_source_sql_page_helper_filters_orders_and_limits_before_materializing(self):
        tree = ast.parse(MODELS_PATH.read_text(encoding="utf-8"), filename=str(MODELS_PATH))
        table = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "FilesTable"
        )
        method = next(
            node for node in table.body
            if isinstance(node, ast.FunctionDef) and node.name == "get_files_page"
        )
        fake_file = types.SimpleNamespace(
            user_id=object(), updated_at=object(), id=object()
        )

        class Column:
            def __init__(self, name):
                self.name = name

            def __eq__(self, other):
                return (self.name, "eq", other)

            def desc(self):
                return (self.name, "desc")

        fake_file.user_id = Column("user_id")
        fake_file.updated_at = Column("updated_at")
        fake_file.id = Column("id")

        class Query:
            def __init__(self):
                self.operations = []
                self.rows = [FileRecord(id=f"row-{index:02}") for index in range(50)]

            def filter(self, expression):
                self.operations.append(("filter", expression))
                return self

            def count(self):
                self.operations.append(("count",))
                return 73

            def order_by(self, *columns):
                self.operations.append(("order_by", columns))
                return self

            def offset(self, value):
                self.operations.append(("offset", value))
                return self

            def limit(self, value):
                self.operations.append(("limit", value))
                return self

            def all(self):
                self.operations.append(("all",))
                return self.rows

        query = Query()

        class Db:
            def query(self, model):
                self.model = model
                return query

        db = Db()

        @contextlib.contextmanager
        def get_db():
            yield db

        method.decorator_list = []
        isolated_class = ast.ClassDef(name="IsolatedFilesTable", bases=[], keywords=[], body=[method], decorator_list=[])
        namespace = {
            "Optional": Optional,
            "File": fake_file,
            "FileModel": self.models["FileModel"],
            "get_db": get_db,
        }
        module = ast.fix_missing_locations(ast.Module(body=[isolated_class], type_ignores=[]))
        exec(compile(module, str(MODELS_PATH), "exec"), namespace)
        count, records = namespace["IsolatedFilesTable"]().get_files_page("member", 2)
        self.assertEqual(count, 73)
        self.assertEqual(len(records), 50)
        self.assertTrue(all(isinstance(record, self.models["FileModel"]) for record in records))
        self.assertIn(("filter", ("user_id", "eq", "member")), query.operations)
        self.assertIn(("order_by", (("updated_at", "desc"), ("id", "desc"))), query.operations)
        self.assertIn(("offset", 50), query.operations)
        self.assertIn(("limit", 50), query.operations)
        self.assertLess(query.operations.index(("limit", 50)), query.operations.index(("all",)))

        query.operations.clear()
        namespace["IsolatedFilesTable"]().get_files_page(None, 1)
        self.assertNotIn("filter", [operation[0] for operation in query.operations])


if __name__ == "__main__":
    unittest.main(verbosity=2)
