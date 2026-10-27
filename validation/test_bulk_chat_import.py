#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-extracted bulk-import checks with an isolated SQLite transaction."""

import ast
import asyncio
import copy
import json
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Optional, Union
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, TypeAdapter, ValidationError, model_validator

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


def extract_forms():
    tree = ast.parse(MODEL.read_text(encoding="utf-8"))
    names = {"ChatForm", "ChatImportForm", "ChatImportItemForm", "ChatsImportForm"}
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in names]
    namespace = {"BaseModel": BaseModel, "ConfigDict": ConfigDict, "Optional": Optional, "StrictBool": StrictBool,
        "model_validator": model_validator,
        "StrictInt": StrictInt, "StrictStr": StrictStr}
    exec(compile(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[])), str(MODEL), "exec"), namespace)
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
        self.namespace = extract(MODEL, {"_chat_text_file_reference", "_valid_text_file_reference_shape", "_lookup_import_file", "_validate_owned_text_file", "_prepare_bulk_chat_import", "import_chats"}, {
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
                "chat": {"title": title, "history": {"messages": {"m1": {"role": "user", "content": "question", "parentId": None}, "m2": {"role": "assistant", "content": "answer", "parentId": "m1"}}, "currentId": "m1"}}, **fields,
                **({"current_message_id": current} if current is not None else {})}

    def rows(self):
        return self.connection.execute("SELECT id,user_id,title,chat,created_at,updated_at,share_id,archived,pinned,meta,folder_id FROM chat ORDER BY rowid").fetchall()

    @staticmethod
    def attached_item(title="Attached"):
        value = BulkChatImportTests.item(title)
        meta = {"name": "synthetic-workshop.txt", "content_type": "text/plain", "size": 77, "data": {}, "file_hash": "a" * 64}
        public_file = {"created_at": 1700000000, "data": {}, "filename": "synthetic-workshop.txt", "hash": None,
                       "id": "file-1", "meta": meta, "updated_at": 1700000001, "user_id": "verified-user"}
        reference = {"error": "", "file": public_file, "id": "file-1", "itemId": "item-1",
                     "name": "synthetic-workshop.txt", "size": 77, "status": "uploaded", "type": "file",
                     "url": "/api/v1/files/file-1"}
        value["chat"]["history"]["messages"]["m1"]["id"] = "m1"
        value["chat"]["history"]["messages"]["m2"]["id"] = "m2"
        value["chat"]["files"] = [copy.deepcopy(reference)]
        value["chat"]["history"]["messages"]["m1"]["files"] = [copy.deepcopy(reference)]
        value["chat"]["messages"] = [copy.deepcopy(value["chat"]["history"]["messages"][key]) for key in ("m1", "m2")]
        value["chat"]["messages"][0]["files"] = [copy.deepcopy(reference)]
        row = SimpleNamespace(id="file-1", user_id="verified-user", access_control=None, data={},
                              filename="synthetic-workshop.txt", hash=None, meta=meta,
                              created_at=1700000000, updated_at=1700000001, path="/never/read")
        return value, row

    def set_file_lookup(self, row):
        calls = []
        def lookup(file_id):
            calls.append(file_id)
            return row
        self.namespace["_lookup_import_file"] = lookup
        return calls

    def test_measured_attached_text_import_uses_one_private_authoritative_lookup(self):
        item, row = self.attached_item()
        calls = self.set_file_lookup(row)
        before = copy.deepcopy(item["chat"])
        result = self.table.import_chats("verified-user", [item])
        self.assertEqual(calls, ["file-1"])
        self.assertEqual(len(result), 1)
        stored = json.loads(self.rows()[0][3])
        before["history"]["currentId"] = "m2"
        self.assertEqual(stored, before)
        self.assertEqual((stored["files"][0]["id"], stored["history"]["messages"]["m1"]["files"][0]["id"], stored["messages"][0]["files"][0]["id"]), ("file-1",) * 3)

    def test_repeated_same_identity_across_batch_is_cached_once(self):
        first, row = self.attached_item("First attached")
        second, _ = self.attached_item("Second attached")
        second["chat"]["files"][0]["itemId"] = "item-2"
        second["chat"]["history"]["messages"]["m1"]["files"][0]["itemId"] = "item-2"
        second["chat"]["messages"][0]["files"][0]["itemId"] = "item-2"
        calls = self.set_file_lookup(row)
        self.assertEqual(len(self.table.import_chats("verified-user", [first, second])), 2)
        self.assertEqual(calls, ["file-1"])
        self.assertEqual(len(self.rows()), 2)

    def test_missing_and_foreign_files_share_fixed_prewrite_failure(self):
        item, row = self.attached_item()
        for record in (None, SimpleNamespace(**{**row.__dict__, "user_id": "another-user"})):
            self.connection.execute("DELETE FROM chat"); self.connection.commit()
            calls = self.set_file_lookup(record)
            with self.assertRaisesRegex(ValueError, "^Attached file import is unsupported\\.$"):
                self.table.import_chats("verified-user", [item])
            self.assertEqual(calls, ["file-1"])
            self.assertEqual(self.rows(), [])
            self.assertEqual(self.opens, 0)

    def test_mismatched_references_and_late_invalid_batch_do_not_lookup_or_write(self):
        valid, row = self.attached_item("First")
        calls = self.set_file_lookup(row)
        mismatch = copy.deepcopy(valid)
        mismatch["chat"]["messages"][0]["files"][0]["id"] = "file-other"
        with self.assertRaises(ValueError):
            self.table.import_chats("verified-user", [valid, mismatch])
        self.assertEqual(calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.opens, 0)
        other, _ = self.attached_item("Other identity")
        for place in (other["chat"]["files"], other["chat"]["history"]["messages"]["m1"]["files"], other["chat"]["messages"][0]["files"]):
            place[0]["id"] = "file-other"
            place[0]["file"]["id"] = "file-other"
            place[0]["url"] = "/api/v1/files/file-other"
        with self.assertRaises(ValueError):
            self.table.import_chats("verified-user", [valid, other])
        self.assertEqual(calls, [])
        self.assertEqual(self.rows(), [])

    def test_spoofed_dto_rights_and_metadata_refuse_after_single_lookup(self):
        for mutation, row_change, lookup_count in (
            (lambda ref: ref["file"].update(filename="other.txt"), None, 0),
            (lambda ref: ref["file"].update(user_id="another-user"), None, 1),
            (lambda ref: (ref.update(size=78), ref["file"]["meta"].update(size=78)), None, 1),
            (lambda ref: None, {"access_control": {"read": ["other"]}}, 1),
            (lambda ref: None, {"data": {"unexpected": True}}, 1),
        ):
            item, row = self.attached_item()
            for reference in (item["chat"]["files"][0], item["chat"]["history"]["messages"]["m1"]["files"][0], item["chat"]["messages"][0]["files"][0]):
                mutation(reference)
            if row_change:
                row = SimpleNamespace(**{**row.__dict__, **row_change})
            calls = self.set_file_lookup(row)
            with self.assertRaisesRegex(ValueError, "^Attached file import is unsupported\\.$"):
                self.table.import_chats("verified-user", [item])
            self.assertEqual(calls, ["file-1"] if lookup_count else [])
            self.assertEqual(self.rows(), [])
            self.assertEqual(self.opens, 0)

    def test_nontext_metadata_extra_keys_and_multiple_items_refuse_before_lookup(self):
        for mutation in (
            lambda refs: [ref["file"]["meta"].update(content_type="application/pdf") for ref in refs],
            lambda refs: [ref.update(collection_name="unmeasured") for ref in refs],
        ):
            item, row = self.attached_item()
            refs = [item["chat"]["files"][0], item["chat"]["history"]["messages"]["m1"]["files"][0], item["chat"]["messages"][0]["files"][0]]
            mutation(refs)
            calls = self.set_file_lookup(row)
            with self.assertRaises(ValueError):
                self.table.import_chats("verified-user", [item])
            self.assertEqual(calls, [])
            self.assertEqual(self.rows(), [])
            self.assertEqual(self.opens, 0)
        item, row = self.attached_item()
        item["chat"]["files"].append(copy.deepcopy(item["chat"]["files"][0]))
        calls = self.set_file_lookup(row)
        with self.assertRaises(ValueError):
            self.table.import_chats("verified-user", [item])
        self.assertEqual(calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.opens, 0)

    def test_no_file_import_keeps_existing_zero_lookup_behavior(self):
        calls = self.set_file_lookup(None)
        self.table.import_chats("verified-user", [self.item("No file")])
        self.assertEqual(calls, [])
        self.assertEqual(len(self.rows()), 1)

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
            self.assertEqual(graph["history"]["messages"]["m1"]["content"], "question")
            self.assertIsNone(row[6])
            self.assertEqual(row[8], 0)

    def test_measured_nullable_defaults_individually(self):
        cases = (("pinned", {"pinned": None}), ("variables", {"variables": None}),
                 ("current_message_id", {"current_message_id": None}), ("created_at", {"created_at": None}),
                 ("updated_at", {"updated_at": None}))
        item_form = extract_forms()["ChatImportItemForm"]
        for index, (field, values) in enumerate(cases):
            item = self.item(field, current=None, **values)
            dto = item_form.model_validate(item)
            self.namespace["_prepare_bulk_chat_import"]([dto.model_dump(exclude_unset=True)])
            self.table.import_chats("verified-user", [item])
            row = self.rows()[index]
            graph = json.loads(row[3])
            with self.subTest(field=field):
                if field == "pinned":
                    self.assertEqual(row[8], 0)
                if field == "current_message_id":
                    self.assertEqual(graph["history"]["currentId"], "m1")
                if field == "created_at":
                    self.assertIsInstance(row[4], int)
                    self.assertEqual(row[5], 456)
                if field == "updated_at":
                    self.assertEqual(row[4], 123)
                    self.assertIsInstance(row[5], int)

    def test_nonempty_variables_and_unmeasured_nulls_remain_refused(self):
        for form in (self.item("Variables", variables={"x": 1}), self.item("Meta", meta=None), self.item("Archive", archived=None)):
            with self.subTest(title=form["chat"]["title"]), self.assertRaises(ValueError):
                self.namespace["_prepare_bulk_chat_import"]([form])
        self.assertEqual(self.opens, 0)

    def test_injected_second_insert_failure_rolls_back_batch(self):
        self.connection.execute("CREATE TRIGGER reject_second BEFORE INSERT ON chat WHEN NEW.title='Fail' BEGIN SELECT RAISE(ABORT, 'synthetic rejection'); END")
        self.connection.commit()
        with self.assertRaisesRegex(RuntimeError, "Bulk chat import failed"):
            self.table.import_chats("verified-user", [self.item("First"), self.item("Fail")])
        self.assertEqual(self.rows(), [])

    def test_unsupported_fields_and_invalid_selection_refuse_before_session(self):
        for item in (self.item("Folder", folder_id="folder"), self.item("Tags", meta={"tags": ["x"]}),
                     self.item("Files", chat={"title": "Files", "files": ["file"]}),
                     self.item("Image content", chat={"title": "Image", "history": {"messages": {"m1": {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "synthetic"}}]}}, "currentId": "m1"}}),
                     self.item("Tool content", chat={"title": "Tool", "history": {"messages": {"m1": {"role": "tool", "content": "result"}}, "currentId": "m1"}}),
                     self.item("Null metadata", meta=None),
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
        form_types = extract_forms()

        class Response:
            def __init__(self, **values): self.values = values

        calls = []
        existing = Model(id="legacy", meta={}, model_dump=lambda: {})
        existing.meta = {}
        # Exercise the route's legacy dispatch with inert dependencies.
        class Chats:
            @staticmethod
            def import_chat(owner, form):
                calls.append((owner, form))
                return existing

        existing.model_dump = lambda: {"id": "legacy", "user_id": "verified", "title": "Old", "chat": {}, "created_at": 1, "updated_at": 1, "archived": False, "meta": {}}
        ns = extract(ROUTER, {"import_chat"}, {"ChatImportForm": form_types["ChatImportForm"],
            "ChatsImportForm": form_types["ChatsImportForm"], "ChatResponse": Response,
            "Chats": Chats, "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda self, **kw: setattr(self, "detail", kw)}),
            "log": SimpleNamespace(exception=lambda _e: None), "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422, HTTP_400_BAD_REQUEST=400),
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "failed"), "Tags": SimpleNamespace(), "Depends": lambda _value: None,
            "get_verified_user": object(), "Union": Union, "Optional": Optional})
        form = form_types["ChatImportForm"](chat={"title": "Old"})
        output = asyncio.run(ns["import_chat"](form, SimpleNamespace(id="verified")))
        self.assertEqual(calls, [("verified", form)])
        self.assertEqual(calls[0][1].chat, {"title": "Old"})
        self.assertEqual(output.values["id"], "legacy")

    def test_bulk_route_returns_array_for_verified_owner(self):
        form_types = extract_forms()
        class Response:
            def __init__(self, **values): self.values = values

        calls = []
        class Chats:
            @staticmethod
            def import_chats(owner, forms):
                calls.append((owner, forms))
                return [Model(id="new-1", user_id=owner, title="One", chat={}, created_at=1, updated_at=1),
                        Model(id="new-2", user_id=owner, title="Two", chat={}, created_at=2, updated_at=2)]

        ns = extract(ROUTER, {"import_chat"}, {"ChatImportForm": form_types["ChatImportForm"],
            "ChatsImportForm": form_types["ChatsImportForm"], "ChatResponse": Response, "Chats": Chats,
            "HTTPException": type("HTTPException", (Exception,), {"__init__": lambda self, **kw: setattr(self, "detail", kw)}),
            "log": SimpleNamespace(exception=lambda _e: None), "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422, HTTP_400_BAD_REQUEST=400),
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "failed"), "Depends": lambda _value: None,
            "get_verified_user": object(), "Union": Union, "Optional": Optional})
        forms = [form_types["ChatImportItemForm"](chat={"title": "One"}), form_types["ChatImportItemForm"](chat={"title": "Two"})]
        result = asyncio.run(ns["import_chat"](form_types["ChatsImportForm"](chats=forms), SimpleNamespace(id="verified")))
        self.assertEqual(calls, [("verified", [{"chat": {"title": "One"}}, {"chat": {"title": "Two"}}])])
        self.assertEqual([item.values["id"] for item in result], ["new-1", "new-2"])

    def test_bulk_dto_rejects_unknown_rights_and_singleton_requires_chat(self):
        form_types = extract_forms()
        with self.assertRaises(ValidationError):
            TypeAdapter(Union[form_types["ChatImportForm"], form_types["ChatsImportForm"]]).validate_python({"title": "missing chat"})
        with self.assertRaises(ValidationError):
            form_types["ChatsImportForm"].model_validate({"chats": [{"chat": {}, "access_control": {"read": ["*-"]}}]})
        adapter = TypeAdapter(Union[form_types["ChatImportForm"], form_types["ChatsImportForm"]])
        self.assertIsInstance(adapter.validate_python({"chats": [{"chat": {}}]}), form_types["ChatsImportForm"])
        with self.assertRaises(ValidationError):
            adapter.validate_python({"chat": {}, "chats": [{"chat": {}}]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
