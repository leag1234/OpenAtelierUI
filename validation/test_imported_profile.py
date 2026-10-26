#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound tests for the opt-in imported profile field prototype."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import types
import unittest
import traceback
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict
from pydantic import field_serializer


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SOURCE = ROOT / "backend/open_webui/utils/imported_profile.py"
USERS_SOURCE = ROOT / "backend/open_webui/models/users.py"
AUTHS_MODEL_SOURCE = ROOT / "backend/open_webui/models/auths.py"
AUTHS_ROUTE_SOURCE = ROOT / "backend/open_webui/routers/auths.py"
AUTHS_SOURCE = ROOT / "backend/open_webui/models/auths.py"


class Result:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount

    def first(self):
        return self.cursor.fetchone()


class SQLiteSession:
    def __init__(self, connection):
        self.connection = connection
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        return Result(self.connection.execute(statement, parameters or {}))

    def commit(self):
        self.connection.commit()
        self.commits += 1

    def rollback(self):
        self.connection.rollback()
        self.rollbacks += 1


class EngineConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def exec_driver_sql(self, statement):
        return self.connection.execute(statement)


class SQLiteEngine:
    class Dialect:
        name = "sqlite"

    dialect = Dialect()

    def __init__(self, path):
        self.path = path
        self.connections = 0

    def connect(self):
        self.connections += 1
        return EngineConnection(sqlite3.connect(self.path))


def load_profile_module():
    module_name = "open_webui.utils.imported_profile_tested"
    fake_sqlalchemy = types.ModuleType("sqlalchemy")
    fake_sqlalchemy.text = lambda statement: statement
    prior = sys.modules.get("sqlalchemy")
    sys.modules["sqlalchemy"] = fake_sqlalchemy
    try:
        spec = importlib.util.spec_from_file_location(module_name, PROFILE_SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if prior is None:
            sys.modules.pop("sqlalchemy", None)
        else:
            sys.modules["sqlalchemy"] = prior


def extract_nodes(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    missing = set(names) - {node.name for node in selected}
    if missing:
        raise AssertionError(f"source definitions missing: {sorted(missing)}")
    code = compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec")
    exec(code, namespace)
    return namespace


def extract_method(path, class_name, method_name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    owner = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(
        node for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )
    method = copy.deepcopy(method)
    method.decorator_list = []
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace


def make_profile_database(path):
    db = sqlite3.connect(path)
    db.execute(
        '''CREATE TABLE "user"(
             id TEXT PRIMARY KEY, name TEXT, email TEXT, role TEXT,
             profile_image_url TEXT, last_active_at INTEGER, updated_at INTEGER,
             created_at INTEGER, bio TEXT, gender TEXT, date_of_birth DATE
           )'''
    )
    db.execute(
        'INSERT INTO "user" VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        ("u1", "Synthetic User", "u1@example.invalid", "user", "/user.png", 1, 2, 3,
         "Original bio", "Original gender", "1994-07-12"),
    )
    db.execute(
        'INSERT INTO "user" VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        ("u2", "Other User", "u2@example.invalid", "user", "/other.png", 1, 2, 3,
         "Other bio", "Other gender", "2001-02-03"),
    )
    db.commit()
    db.close()


class ImportedProfileStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "users.db"
        make_profile_database(self.db_path)
        self.module = load_profile_module()
        self.module._text = lambda statement: statement
        self.old_flag = os.environ.get(self.module.PROFILE_FIELDS_ENV)

    def tearDown(self):
        if self.old_flag is None:
            os.environ.pop(self.module.PROFILE_FIELDS_ENV, None)
        else:
            os.environ[self.module.PROFILE_FIELDS_ENV] = self.old_flag
        self.temp.cleanup()

    def test_switch_is_exact_and_default_off_performs_no_schema_read(self):
        os.environ.pop(self.module.PROFILE_FIELDS_ENV, None)
        engine = SQLiteEngine(self.db_path)
        self.assertFalse(self.module.user_profile_fields_enabled())
        self.assertFalse(self.module.validate_prepared_user_profile_schema(engine))
        self.assertEqual(engine.connections, 0)
        os.environ[self.module.PROFILE_FIELDS_ENV] = "0"
        self.assertFalse(self.module.validate_prepared_user_profile_schema(engine))
        self.assertEqual(engine.connections, 0)
        os.environ[self.module.PROFILE_FIELDS_ENV] = "true"
        with self.assertRaisesRegex(RuntimeError, "must be exactly 1"):
            self.module.user_profile_fields_enabled()

    def test_enabled_schema_check_is_read_only_and_fails_clearly(self):
        os.environ[self.module.PROFILE_FIELDS_ENV] = "1"
        engine = SQLiteEngine(self.db_path)
        self.assertTrue(self.module.validate_prepared_user_profile_schema(engine))
        self.assertEqual(engine.connections, 1)
        wrong_engine = type("WrongEngine", (), {"dialect": type("D", (), {"name": "postgresql"})()})()
        with self.assertRaisesRegex(RuntimeError, "prepared SQLite"):
            self.module.validate_prepared_user_profile_schema(wrong_engine)

        wrong = Path(self.temp.name) / "wrong.db"
        db = sqlite3.connect(wrong)
        db.execute('CREATE TABLE "user" (id TEXT, bio TEXT NOT NULL, gender TEXT, date_of_birth DATE)')
        db.close()
        with self.assertRaisesRegex(RuntimeError, "requires prepared nullable"):
            self.module.validate_prepared_user_profile_schema(SQLiteEngine(wrong))

        missing = Path(self.temp.name) / "missing.db"
        db = sqlite3.connect(missing)
        db.execute('CREATE TABLE "user" (id TEXT, bio TEXT, gender TEXT)')
        db.close()
        with self.assertRaisesRegex(RuntimeError, "requires prepared nullable"):
            self.module.validate_prepared_user_profile_schema(SQLiteEngine(missing))

    def test_one_lookup_and_one_atomic_update_preserve_other_accounts(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        session = SQLiteSession(connection)
        profile = self.module.read_user_profile_fields(session, "u1")
        self.assertEqual(len(session.statements), 1)
        self.assertEqual(profile, {
            "bio": "Original bio",
            "gender": "Original gender",
            "date_of_birth": datetime.date(1994, 7, 12),
        })

        self.assertTrue(self.module.update_user_profile_fields(
            session, "u1", "Synthetic Updated", "/new.png", "Updated bio", None,
            datetime.date(1995, 1, 2),
        ))
        self.assertEqual(len(session.statements), 2)
        self.assertEqual(session.commits, 1)
        saved = connection.execute(
            'SELECT name,profile_image_url,bio,gender,date_of_birth FROM "user" WHERE id="u1"'
        ).fetchone()
        self.assertEqual(tuple(saved), (
            "Synthetic Updated", "/new.png", "Updated bio", None, "1995-01-02"
        ))
        other = connection.execute(
            'SELECT name,profile_image_url,bio,gender,date_of_birth FROM "user" WHERE id="u2"'
        ).fetchone()
        self.assertEqual(tuple(other), (
            "Other User", "/other.png", "Other bio", "Other gender", "2001-02-03"
        ))

        self.assertTrue(self.module.update_user_profile_fields(
            session, "u1", "Synthetic Updated", "/new.png", None, None, None
        ))
        cleared = connection.execute(
            'SELECT bio,gender,date_of_birth FROM "user" WHERE id="u1"'
        ).fetchone()
        self.assertEqual(tuple(cleared), (None, None, None))
        connection.close()

    def test_invalid_update_is_rejected_without_partial_write_and_sql_error_rolls_back(self):
        connection = sqlite3.connect(self.db_path)
        session = SQLiteSession(connection)
        with self.assertRaisesRegex(RuntimeError, "oversized"):
            self.module.update_user_profile_fields(
                session, "u1", "Changed", "/changed.png", "x" * 8193, None, None
            )
        self.assertEqual(connection.execute('SELECT name FROM "user" WHERE id="u1"').fetchone()[0], "Synthetic User")

        secret_marker = "synthetic-profile-parameter-marker"
        connection.execute(
            '''CREATE TRIGGER fail_profile_update BEFORE UPDATE ON "user"
               WHEN OLD.id='u1' BEGIN SELECT RAISE(ABORT,'synthetic-profile-parameter-marker'); END'''
        )
        connection.commit()
        with self.assertRaises(self.module.ImportedProfileError) as caught:
            self.module.update_user_profile_fields(
                session, "u1", "Changed", "/changed.png", "Changed bio", None, None
            )
        rendered = "".join(
            traceback.format_exception(
                type(caught.exception), caught.exception, caught.exception.__traceback__
            )
        )
        self.assertNotIn(secret_marker, rendered)
        self.assertIn("prepared user profile update is unavailable", rendered)
        self.assertGreaterEqual(session.rollbacks, 1)
        connection.close()

    def test_invalid_date_and_non_text_are_rejected(self):
        for value in ("1994-7-12", datetime.datetime(1994, 7, 12), 123):
            with self.subTest(value=repr(value)):
                with self.assertRaises(RuntimeError):
                    self.module._validate_profile_values({"bio": None, "gender": None, "date_of_birth": value})


class ImportedProfileSourceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile_module()
        self.profile._text = lambda statement: statement
        class UserModel(BaseModel):
            id: str
            name: str
            email: str
            role: str = "pending"
            profile_image_url: str
            last_active_at: int
            updated_at: int
            created_at: int
            model_config = ConfigDict(from_attributes=True)

        self.UserModel = UserModel
        self.ImportedProfileUserModel = extract_nodes(
            USERS_SOURCE,
            {"ImportedProfileUserModel"},
            {"UserModel": UserModel, "Optional": Optional, "date": datetime.date,
             "field_serializer": field_serializer},
        )["ImportedProfileUserModel"]
        self.ImportedProfileUserModel.model_rebuild(
            _types_namespace={"Optional": Optional, "date": datetime.date}
        )

    def test_database_and_legacy_user_models_remain_profile_free(self):
        tree = ast.parse(USERS_SOURCE.read_text(encoding="utf-8"), filename=str(USERS_SOURCE))
        user_table = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "User")
        mapped_names = {
            target.id
            for node in user_table.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertTrue({"bio", "gender", "date_of_birth"}.isdisjoint(mapped_names))

        models = extract_nodes(
            USERS_SOURCE,
            {"UserSettings", "UserModel"},
            {"BaseModel": BaseModel, "ConfigDict": ConfigDict, "Optional": Optional},
        )
        self.assertTrue({"bio", "gender", "date_of_birth"}.isdisjoint(models["UserModel"].model_fields))
        self.assertTrue({"bio", "gender", "date_of_birth"}.issubset(self.ImportedProfileUserModel.model_fields))

    def test_real_user_lookup_method_keeps_legacy_off_and_returns_typed_projection_on(self):
        namespace = {
            "Optional": Optional,
            "UserModel": self.UserModel,
            "ImportedProfileUserModel": self.ImportedProfileUserModel,
            "User": object(),
            "ImportedProfileError": self.profile.ImportedProfileError,
            "read_user_profile_fields": self.profile.read_user_profile_fields,
        }
        extract_method(USERS_SOURCE, "UsersTable", "get_user_by_id", namespace)
        lookup = namespace["get_user_by_id"]

        class Row:
            id = "u1"
            name = "Synthetic User"
            email = "u1@example.invalid"
            role = "user"
            profile_image_url = "/user.png"
            last_active_at = 1
            updated_at = 2
            created_at = 3

        class Query:
            def __init__(self):
                self.row = Row()

            def filter_by(self, **kwargs):
                self.filter = kwargs
                return self

            def first(self):
                return self.row

        class Database:
            def __init__(self, session):
                self.session = session
                self.query_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def query(self, _model):
                self.query_calls += 1
                return Query()

            def execute(self, statement, parameters=None):
                return self.session.execute(statement, parameters)

            def commit(self):
                return self.session.commit()

            def rollback(self):
                return self.session.rollback()

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE "user" (id TEXT,bio TEXT,gender TEXT,date_of_birth DATE)')
        connection.execute('INSERT INTO "user" VALUES (?,?,?,?)', ("u1", "Bio", "Gender", "2000-01-02"))
        session = SQLiteSession(connection)
        database = Database(session)
        flag = {"enabled": False}
        namespace["user_profile_fields_enabled"] = lambda: flag["enabled"]
        namespace["get_db"] = lambda: database

        result = lookup(object(), "u1")
        self.assertIs(type(result), self.UserModel)
        self.assertFalse(hasattr(result, "bio"))
        self.assertEqual(session.statements, [])
        self.assertEqual(database.query_calls, 1)

        flag["enabled"] = True
        projected = lookup(object(), "u1")
        self.assertIs(type(projected), self.ImportedProfileUserModel)
        self.assertEqual(projected.bio, "Bio")
        self.assertEqual(projected.gender, "Gender")
        self.assertEqual(projected.date_of_birth, datetime.date(2000, 1, 2))
        self.assertEqual(json.loads(json.dumps(projected.model_dump()))["date_of_birth"], "2000-01-02")
        self.assertEqual(len(session.statements), 1)

        namespace["read_user_profile_fields"] = lambda *_: (_ for _ in ()).throw(
            RuntimeError("prepared profile schema is unavailable")
        )
        with self.assertRaisesRegex(self.profile.ImportedProfileError, "lookup is unavailable"):
            lookup(object(), "u1")
        connection.close()

    def test_real_email_lookup_preserves_default_off_and_projects_one_profile_query(self):
        namespace = {
            "Optional": Optional,
            "UserModel": self.UserModel,
            "ImportedProfileUserModel": self.ImportedProfileUserModel,
            "User": object(),
            "ImportedProfileError": self.profile.ImportedProfileError,
            "read_user_profile_fields": self.profile.read_user_profile_fields,
        }
        extract_method(USERS_SOURCE, "UsersTable", "get_user_by_email", namespace)
        lookup = namespace["get_user_by_email"]

        class Row:
            id = "u1"
            name = "Synthetic User"
            email = "u1@example.invalid"
            role = "user"
            profile_image_url = "/user.png"
            last_active_at = 1
            updated_at = 2
            created_at = 3

        class Query:
            def __init__(self, database):
                self.database = database
                self.filters = {}

            def filter_by(self, **kwargs):
                self.filters.update(kwargs)
                self.database.filter_calls.append(dict(kwargs))
                return self

            def first(self):
                email = self.filters["email"]
                return self.database.row if email == self.database.row.email else None

        class Database:
            def __init__(self, session):
                self.session = session
                self.row = Row()
                self.query_calls = 0
                self.filter_calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def query(self, _model):
                self.query_calls += 1
                return Query(self)

            def execute(self, statement, parameters=None):
                return self.session.execute(statement, parameters)

        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE "user" (id TEXT,bio TEXT,gender TEXT,date_of_birth DATE)')
        connection.execute(
            'INSERT INTO "user" VALUES (?,?,?,?)',
            ("u1", "Synthetic profile", "Synthetic gender", "2000-01-02"),
        )
        session = SQLiteSession(connection)
        database = Database(session)
        enabled = {"value": False}
        namespace["user_profile_fields_enabled"] = lambda: enabled["value"]
        namespace["get_db"] = lambda: database

        legacy = lookup(object(), "u1@example.invalid")
        self.assertIs(type(legacy), self.UserModel)
        self.assertEqual(database.filter_calls[-1], {"email": "u1@example.invalid"})
        self.assertEqual(session.statements, [])
        self.assertIsNone(lookup(object(), "U1@example.invalid"))
        self.assertEqual(session.statements, [])

        enabled["value"] = True
        projected = lookup(object(), email="u1@example.invalid")
        self.assertIs(type(projected), self.ImportedProfileUserModel)
        self.assertEqual(projected.bio, "Synthetic profile")
        self.assertEqual(projected.gender, "Synthetic gender")
        self.assertEqual(projected.date_of_birth, datetime.date(2000, 1, 2))
        self.assertEqual(len(session.statements), 1)
        self.assertEqual(database.query_calls, 3)

        self.assertIsNone(lookup(object(), "missing@example.invalid"))
        self.assertEqual(len(session.statements), 1)
        self.assertEqual(database.query_calls, 4)

        namespace["read_user_profile_fields"] = lambda *_: (_ for _ in ()).throw(
            RuntimeError("synthetic selected profile failure")
        )
        with self.assertRaisesRegex(self.profile.ImportedProfileError, "lookup is unavailable") as caught:
            lookup(object(), "u1@example.invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertEqual(database.query_calls, 5)
        connection.close()

    def test_actual_profile_forms_response_and_own_profile_route_are_opt_in(self):
        model_namespace = {
            "BaseModel": BaseModel,
            "Optional": Optional,
            "date": datetime.date,
            "field_validator": __import__("pydantic").field_validator,
        }
        extract_nodes(
            AUTHS_MODEL_SOURCE,
            {"UpdateProfileForm", "ImportedProfileUpdateForm", "UserResponse", "ImportedProfileUserResponse"},
            model_namespace,
        )
        for model_name in ("ImportedProfileUpdateForm", "ImportedProfileUserResponse"):
            model_namespace[model_name].model_rebuild(
                _types_namespace={"Optional": Optional, "date": datetime.date}
            )
        form = model_namespace["ImportedProfileUpdateForm"](
            name="Synthetic User", profile_image_url="/user.png", bio=None,
            gender="Nonbinary", date_of_birth="2000-01-02",
        )
        self.assertIsNone(form.bio)
        self.assertEqual(form.date_of_birth, datetime.date(2000, 1, 2))
        self.assertTrue({"bio", "gender", "date_of_birth"}.isdisjoint(
            model_namespace["ImportedProfileUpdateForm"].model_json_schema().get("required", [])
        ))
        with self.assertRaises(ValueError):
            model_namespace["ImportedProfileUpdateForm"](
                name="Synthetic User", profile_image_url="/user.png", bio="x" * 8193
            )

        def Depends(value):
            return None

        class HTTPException(Exception):
            def __init__(self, status_code, detail=None):
                self.status_code = status_code
                self.detail = detail

        class Errors:
            @staticmethod
            def DEFAULT():
                return "error"

            INVALID_CRED = "invalid credentials"

        class UsersStub:
            call = None

            @classmethod
            def update_imported_user_profile_by_id(cls, user_id, **fields):
                cls.call = (user_id, fields)
                return {"saved": True}

            @classmethod
            def update_user_by_id(cls, user_id, fields):
                cls.call = (user_id, fields)
                return {"legacy": True}

        routes = extract_nodes(
            AUTHS_ROUTE_SOURCE,
            {"update_profile"},
            {
                "Depends": Depends,
                "ProfileUpdateForm": model_namespace["ImportedProfileUpdateForm"],
                "get_verified_user": object(),
                "Users": UsersStub,
                "PROFILE_FIELDS_ENABLED": True,
                "HTTPException": HTTPException,
                "ERROR_MESSAGES": Errors,
            },
        )
        current = types.SimpleNamespace(id="u1")
        response = asyncio.run(routes["update_profile"](form, current))
        self.assertEqual(response, {"saved": True})
        self.assertEqual(UsersStub.call, ("u1", {
            "name": "Synthetic User", "profile_image_url": "/user.png", "bio": None,
            "gender": "Nonbinary", "date_of_birth": datetime.date(2000, 1, 2),
        }))

    def test_actual_session_user_response_adds_profile_fields_only_when_enabled(self):
        class ResponseStub:
            def __init__(self):
                self.cookies = []

            def set_cookie(self, **kwargs):
                self.cookies.append(kwargs)

        class RequestStub:
            app = types.SimpleNamespace(state=types.SimpleNamespace(config=types.SimpleNamespace(
                JWT_EXPIRES_IN="1h", USER_PERMISSIONS={}
            )))

        def Depends(value):
            return None

        user = types.SimpleNamespace(
            id="u1", email="u1@example.invalid", name="Synthetic User", role="user",
            profile_image_url="/user.png", bio="Synthetic bio", gender=None,
            date_of_birth=datetime.date(2000, 1, 2),
        )

        def call(enabled):
            namespace = extract_nodes(
                AUTHS_ROUTE_SOURCE,
                {"get_session_user"},
                {
                    "Depends": Depends,
                    "Request": object,
                    "Response": object,
                    "get_current_user": object(),
                    "get_validated_session_context": lambda request, user_id: ("retained-token", 2000, "stamp"),
                    "datetime": datetime,
                    "time": types.SimpleNamespace(time=lambda: 1000),
                    "parse_duration": lambda value: None,
                    "create_token": lambda **kwargs: "unexpected",
                    "get_permissions": lambda user_id, permissions: {"profile": True},
                    "WEBUI_AUTH_COOKIE_SAME_SITE": "lax",
                    "WEBUI_AUTH_COOKIE_SECURE": False,
                    "PROFILE_FIELDS_ENABLED": enabled,
                },
            )
            response = ResponseStub()
            result = asyncio.run(namespace["get_session_user"](RequestStub(), response, user))
            self.assertEqual(len(response.cookies), 1)
            return result

        enabled = call(True)
        self.assertEqual(enabled["bio"], "Synthetic bio")
        self.assertIsNone(enabled["gender"])
        self.assertEqual(enabled["date_of_birth"], datetime.date(2000, 1, 2))
        self.assertEqual(enabled["token"], "retained-token")
        legacy = call(False)
        self.assertNotIn("bio", legacy)
        self.assertNotIn("gender", legacy)
        self.assertNotIn("date_of_birth", legacy)

    def test_legacy_sync_and_awaitable_plugin_lookup_preserve_typed_profile_values(self):
        compat_path = ROOT / "backend/open_webui/utils/plugin_user_compat.py"
        spec = importlib.util.spec_from_file_location("profile_plugin_compat_test", compat_path)
        compat = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(compat)
        profile_user = self.ImportedProfileUserModel.model_validate({
            "id": "u1", "name": "Synthetic User", "email": "u1@example.invalid",
            "role": "user", "profile_image_url": "/user.png", "last_active_at": 1,
            "updated_at": 2, "created_at": 3, "bio": "Synthetic bio",
            "gender": "Nonbinary", "date_of_birth": datetime.date(2000, 1, 2),
        })

        class UsersStub:
            def get_user_by_id(self, id):
                import time
                time.sleep(0.04)
                return profile_user if id == "u1" else None

        def make_plugin(function_source):
            module = types.ModuleType("synthetic_profile_plugin")
            module.Users = UsersStub()
            module.Pipe = type("Pipe", (), {})
            exec(function_source, module.__dict__)
            self.assertTrue(compat.adapt_pipe_module(module, module.Users))
            return module

        sync_module = make_plugin(
            "def pipe(**params):\n    return Users.get_user_by_id(id='u1').date_of_birth.isoformat()\n"
        )
        async_module = make_plugin(
            "async def pipe(**params):\n    user = await Users.get_user_by_id(id='u1')\n    return {'bio': user.bio, 'gender': user.gender, 'date_of_birth': user.date_of_birth.isoformat()}\n"
        )

        async def run_pipe(module):
            return await compat.execute_pipe(
                module.pipe,
                {},
                lambda name: module if name == module.__name__ else None,
            )

        self.assertEqual(asyncio.run(run_pipe(sync_module)), "2000-01-02")
        self.assertEqual(asyncio.run(run_pipe(async_module)), {
            "bio": "Synthetic bio", "gender": "Nonbinary", "date_of_birth": "2000-01-02"
        })

    def test_authentication_wrappers_propagate_only_profile_failures(self):
        profile_error = self.profile.ImportedProfileError("prepared profile unavailable")

        class Query:
            def filter_by(self, **kwargs):
                return self

            def first(self):
                return types.SimpleNamespace(id="u1", password="hashed")

        class Database:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def query(self, _model):
                return Query()

        @contextlib.contextmanager
        def get_db():
            yield Database()

        class UsersStub:
            error = profile_error

            @classmethod
            def get_user_by_id(cls, _user_id):
                raise cls.error

        class LogStub:
            @staticmethod
            def info(_message):
                return None

        namespace = {
            "Optional": Optional,
            "UserModel": self.UserModel,
            "ImportedProfileError": self.profile.ImportedProfileError,
            "get_db": get_db,
            "Auth": object(),
            "Users": UsersStub,
            "verify_password": lambda password, hashed: True,
            "log": LogStub(),
        }
        extract_method(AUTHS_SOURCE, "AuthsTable", "authenticate_user", namespace)
        extract_method(AUTHS_SOURCE, "AuthsTable", "authenticate_user_by_trusted_header", namespace)
        for method_name, args in (
            ("authenticate_user", (object(), "u1@example.invalid", "password")),
            ("authenticate_user_by_trusted_header", (object(), "u1@example.invalid")),
        ):
            with self.subTest(method=method_name):
                with self.assertRaises(self.profile.ImportedProfileError) as caught:
                    namespace[method_name](*args)
                self.assertIs(caught.exception, profile_error)

        UsersStub.error = LookupError("ordinary legacy failure")
        self.assertIsNone(namespace["authenticate_user"](object(), "u1@example.invalid", "password"))
        self.assertIsNone(namespace["authenticate_user_by_trusted_header"](object(), "u1@example.invalid"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
