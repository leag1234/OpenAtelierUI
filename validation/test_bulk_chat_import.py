#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted bulk-import checks with an isolated SQLite transaction."""

import ast
import asyncio
import json
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
MODEL = BASE / "backend/open_webui/models/chats.py"
ROUTER = BASE / "backend/open_webui/routers/chats.py"


def extract(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            nodes.append(node)
        if isinstance(node, ast.ClassDef) and node.name == "ChatTable":
            method = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "import_chats")
            nodes.append(ast.ClassDef(name="ChatTable", bases=[], keywords=[], body=[method], decorator_list=[]))
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                node.decorator_list = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)
    return namespace


class Model:
    def __init__(self, **values):
        self.values = values

    def model_dump(self):
        return self.values


class Row:
    def __init__(self, **values):
        self.values = values


class BulkChatImportTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE chat (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, chat TEXT, created_at INTEGER, updated_at INTEGER, share_id TEXT, archived INTEGER, pinned INTEGER, meta TEXT, folder_id TEXT)")
        self.opens = 0

        class Database:
            def add_all(inner, rows):
                for row in rows:
                    values = row.values
                    inner.connection.execute("INSERT INTO chat VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
                        values["id"], values["user_id"], values["title"], json.dumps(values["chat"]), values["created_at"], values["updated_at"], values["share_id"], values["archived"], values["pinned"], json.dumps(values["meta"]), values["folder_id"]))

            def commit(inner):
                inner.connection.commit()

            def rollback(inner):
                inner.connection.rollback()

        @contextmanager
        def get_db():
            self.opens += 1
            yield Database()

        Database.connection = self.connection
        self.namespace = extract(MODEL, {"_prepare_bulk_chat_import", "import_chats"}, {
            "json": json, "copy": __import__("copy"), "time": __import__("time"),
            "uuid": __import__("uuid"), "get_db": get_db, "Chat": Row,
            "ChatModel": Model,
        })
        self.table = self.namespace["ChatTable"]()

    def tearDown(self):
        self.connection.close()

    @staticmethod
    def item(title, current="m2", **fields):
        return {"id": "source-id", "user_id": "untrusted-owner", "created_at": 123, "updated_at": 456,
                "chat": {"title": title, "history": {"messages": {"m1": {"parentId": None}, "m2": {"parentId": "m1"}}, "currentId": "m1"}}, **fields,
                **({"current_message_id": current} if current is not None else {})}

    def rows(self):
        return self.connection.execute("SELECT id,user_id,title,chat,created_at,updated_at,share_id,archived,pinned,meta,folder_id FROM chat ORDER BY rowid").fetchall()

    def test_two_chat_imports_preserve_graph_and_force_private_owner(self):
        result = self.table.import_chats("verified-user", [self.item("First"), self.item("Second")])
        rows = self.rows()
        self.assertEqual(len(result), 2)
        self.assertEqual(len({row[0] for row in rows}), 2)
        self.assertTrue(all(row[1] == "verified-user" for row in rows))
        self.assertEqual([row[4:6] for row in rows], [(123, 456), (123, 456)])
        for row in rows:
            graph = json.loads(row[3])
            self.assertEqual(graph["history"]["currentId"], "m2")
            self.assertEqual(graph["history"]["messages"]["m2"]["parentId"], "m1")
            self.assertIsNone(row[6])
            self.assertEqual(row[8], 0)

    def test_injected_second_insert_failure_rolls_back_batch(self):
        self.connection.execute("CREATE TRIGGER reject_second BEFORE INSERT ON chat WHEN NEW.title='Fail' BEGIN SELECT RAISE(ABORT, 'synthetic rejection'); END")
        self.connection.commit()
        with self.assertRaisesRegex(RuntimeError, "Bulk chat import failed"):
            self.table.import_chats("verified-user", [self.item("First"), self.item("Fail")])
        self.assertEqual(self.rows(), [])

    def test_unsupported_fields_and_invalid_selection_refuse_before_session(self):
        for item in (self.item("Folder", folder_id="folder"), self.item("Tags", meta={"tags": ["x"]}),
                     self.item("Files", chat={"title": "Files", "files": ["file"]}),
                     self.item("Missing selection", current="absent"),
                     self.item("Malformed graph", chat={"title": "Bad", "history": {"messages": None}})):
            with self.subTest(item=item["chat"]["title"]), self.assertRaises(ValueError):
                self.namespace["_prepare_bulk_chat_import"]([item])
        self.assertEqual(self.opens, 0)
        self.assertEqual(self.rows(), [])

    def test_batch_count_bytes_and_message_caps(self):
        validate = self.namespace["_prepare_bulk_chat_import"]
        for batch in ([], [self.item(str(i)) for i in range(17)], [self.item("x", current=None, chat={"title": "x", "history": {"messages": {str(i): {} for i in range(4097)}}})]):
            with self.subTest(size=len(batch)), self.assertRaises(ValueError):
                validate(batch)
        with self.assertRaisesRegex(ValueError, "4 MiB"):
            validate([self.item("x", current=None, chat={"title": "x" * (4 * 1024 * 1024)})])
        self.assertEqual(self.opens, 0)

    def test_singleton_route_branch_remains_legacy_import(self):
        class ChatImportForm:
            def __init__(self, **values): self.values = values

        class Response:
            def __init__(self, **values): self.values = values

        calls = []
        existing = Model(id="legacy", meta={}, model_dump=lambda: {})
        existing.meta = {}
        # Exercise the route's legacy dispatch with inert dependencies.
        class Chats:
            @staticmethod
            def import_chat(owner, form):
                calls.append((owner, form.values))
                return existing

        existing.model_dump = lambda: {"id": "legacy", "user_id": "verified", "title": "Old", "chat": {}, "created_at": 1, "updated_at": 1, "archived": False, "meta": {}}
        ns = extract(ROUTER, {"import_chat"}, {"ChatImportForm": ChatImportForm, "ChatResponse": Response,
            "Chats": Chats, "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda self, **kw: setattr(self, "detail", kw)}),
            "log": SimpleNamespace(exception=lambda _e: None), "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422, HTTP_400_BAD_REQUEST=400),
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "failed"), "Tags": SimpleNamespace(), "Depends": lambda _value: None,
            "get_verified_user": object()})
        output = asyncio.run(ns["import_chat"]({"chat": {"title": "Old"}}, SimpleNamespace(id="verified")))
        self.assertEqual(calls, [("verified", {"chat": {"title": "Old"}})])
        self.assertEqual(output.values["id"], "legacy")

    def test_bulk_route_returns_array_for_verified_owner(self):
        class Response:
            def __init__(self, **values): self.values = values

        calls = []
        class Chats:
            @staticmethod
            def import_chats(owner, forms):
                calls.append((owner, forms))
                return [Model(id="new-1", user_id=owner, title="One", chat={}, created_at=1, updated_at=1),
                        Model(id="new-2", user_id=owner, title="Two", chat={}, created_at=2, updated_at=2)]

        ns = extract(ROUTER, {"import_chat"}, {"ChatResponse": Response, "Chats": Chats,
            "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda self, **kw: setattr(self, "detail", kw)}),
            "log": SimpleNamespace(exception=lambda _e: None), "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422, HTTP_400_BAD_REQUEST=400),
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "failed"), "Depends": lambda _value: None,
            "get_verified_user": object()})
        forms = [{"chat": {"title": "One"}}, {"chat": {"title": "Two"}}]
        result = asyncio.run(ns["import_chat"]({"chats": forms}, SimpleNamespace(id="verified")))
        self.assertEqual(calls, [("verified", forms)])
        self.assertEqual([item.values["id"] for item in result], ["new-1", "new-2"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
