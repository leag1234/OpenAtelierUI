# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound tests for the bounded private saved-chat compatibility slice."""

import ast
import asyncio
import copy
import json
import pathlib
import unittest
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "chats.py"
ROUTE_PATH = ROOT / "routers" / "chats.py"


def _function(path, name, namespace, preserve_defaults=False):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        item for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
    )
    node.decorator_list = []
    node.returns = None
    node.type_comment = None
    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        arg.annotation = None
    node.args.vararg = None
    node.args.kwarg = None
    if not preserve_defaults:
        node.args.defaults = []
        node.args.kw_defaults = [None] * len(node.args.kwonlyargs)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def _method(path, class_name, method_name, namespace, preserve_defaults=False):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    method.decorator_list = []
    method.returns = None
    for arg in (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs):
        arg.annotation = None
    method.args.vararg = None
    method.args.kwarg = None
    if not preserve_defaults:
        method.args.defaults = []
        method.args.kw_defaults = [None] * len(method.args.kwonlyargs)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[method_name]


def _chat_model():
    tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"), filename=str(MODEL_PATH))
    node = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "ChatModel")
    namespace = {"BaseModel": BaseModel, "ConfigDict": ConfigDict, "Optional": Optional}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(MODEL_PATH), "exec"), namespace)
    return namespace["ChatModel"]


def _model_helpers():
    ns = {"copy": copy, "SAVED_WORK_META_KEY": "_mdk_private_saved_work"}
    names = (
        "_saved_work_marker",
        "_new_saved_work_meta",
        "_public_chat_meta",
        "_saved_work_last_read",
        "_updated_saved_work_read_meta",
        "_normalize_private_chat_authors",
        "_project_private_chat_authors",
        "_chat_current_message_id",
    )
    for name in names:
        _function(MODEL_PATH, name, ns, preserve_defaults=True)
    return ns


def _response_helper():
    ns = {"copy": copy, "PrivateChatResponse": lambda **values: values}
    helpers = _model_helpers()
    ns.update({name: helpers[name] for name in (
        "_saved_work_marker", "_public_chat_meta", "_chat_current_message_id",
        "_project_private_chat_authors",
    )})
    return _function(ROUTE_PATH, "_saved_chat_response", ns, preserve_defaults=True)


class _DB:
    def __init__(self, item=None):
        self.item = item
        self.added = None
        self.commits = 0
        self.filters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def add(self, item):
        self.added = item

    def commit(self):
        self.commits += 1

    def refresh(self, _item):
        pass

    def query(self, _model):
        return self

    def filter_by(self, **filters):
        self.filters = filters
        return self

    def first(self):
        if self.filters and self.item and all(
            getattr(self.item, key) == value for key, value in self.filters.items()
        ):
            return self.item
        return None

    def all(self):
        return getattr(self, "rows", [])

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def with_entities(self, *_args):
        return self

    def offset(self, *_args):
        return self

    def limit(self, *_args):
        return self


class SavedChatCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.helpers = _model_helpers()
        self.actor = SimpleNamespace(id="member-1", name="Synthetic member", role="user")

    def test_normalization_preserves_branch_graph_and_resets_claimed_authors(self):
        source = {
            "history": {"currentId": "chosen", "messages": {
                "question": {"id": "question", "role": "user", "content": "Q", "user_id": "other", "user": {"id": "other", "name": "Claim"}},
                "left": {"id": "left", "role": "assistant", "parentId": "question", "content": "A", "user_id": "fake"},
                "chosen": {"id": "chosen", "role": "assistant", "parentId": "question", "content": "B", "user_id": "missing", "user": {"name": "Bad"}},
            }},
            "messages": [
                {"id": "question", "role": "user", "user_id": "other"},
                {"id": "chosen", "role": "assistant", "user_id": "fake"},
            ],
        }
        result = self.helpers["_normalize_private_chat_authors"](source, self.actor)
        self.assertEqual(result["history"]["currentId"], "chosen")
        self.assertEqual(set(result["history"]["messages"]), {"question", "left", "chosen"})
        for collection in (result["history"]["messages"].values(), result["messages"]):
            for message in collection:
                self.assertEqual(message["user_id"], "member-1")
                if message["role"] == "user":
                    self.assertEqual(message["user"], {"id": "member-1", "name": "Synthetic member"})
                else:
                    self.assertNotIn("user", message)
        self.assertEqual(source["history"]["messages"]["question"]["user_id"], "other")

    def test_missing_actor_name_uses_one_cached_lookup_across_both_message_views(self):
        calls = []
        actor = SimpleNamespace(id="member-1", name=None)
        def lookup(user_id):
            calls.append(user_id)
            return SimpleNamespace(name="Resolved synthetic member")
        chat = {"history": {"messages": {"q": {"role": "user"}, "q2": {"role": "user"}}},
                "messages": [{"role": "user"}, {"role": "user"}]}
        result = self.helpers["_normalize_private_chat_authors"](chat, actor, lookup)
        self.assertEqual(calls, ["member-1"])
        self.assertEqual(result["history"]["messages"]["q"]["user"]["name"], "Resolved synthetic member")
        self.assertEqual(result["messages"][1]["user"]["name"], "Resolved synthetic member")

    def test_metadata_marker_is_bounded_hidden_and_collision_safe(self):
        h = self.helpers
        meta = h["_new_saved_work_meta"]({"tags": ["kept"]})
        marker = meta["_mdk_private_saved_work"]
        self.assertEqual(marker, {"version": 1, "last_read_at": None})
        self.assertLessEqual(len(json.dumps(marker).encode("utf-8")), 1024)
        self.assertEqual(h["_public_chat_meta"](meta), {"tags": ["kept"]})
        collision = {"tags": ["kept"], "_mdk_private_saved_work": {"custom": "opaque"}}
        self.assertEqual(h["_new_saved_work_meta"](collision), collision)
        self.assertEqual(h["_public_chat_meta"](collision), collision)
        self.assertIsNone(h["_updated_saved_work_read_meta"](collision, 100))
        updated = h["_updated_saved_work_read_meta"](meta, 200)
        self.assertEqual(h["_saved_work_last_read"](updated), 200)
        self.assertEqual(updated["tags"], ["kept"])
        self.assertIsNone(h["_saved_work_last_read"]({"_mdk_private_saved_work": {"version": 1, "last_read_at": "bad"}}))
        self.assertIsNone(h["_saved_work_marker"]({"_mdk_private_saved_work": {"version": True, "last_read_at": 1}}))

    def test_storage_insert_stamps_only_new_private_rows_and_keeps_owner(self):
        h = self.helpers
        db = _DB()
        class Row:
            def __init__(self, **kwargs): self.__dict__.update(kwargs)
        method = _method(MODEL_PATH, "ChatTable", "insert_new_chat", {
            "get_db": lambda: db, "ChatModel": _chat_model(), "Chat": Row,
            "uuid": SimpleNamespace(uuid4=lambda: "chat-1"),
            "time": SimpleNamespace(time=lambda: 100),
            "_new_saved_work_meta": h["_new_saved_work_meta"],
        }, preserve_defaults=True)
        form = SimpleNamespace(chat={"title": "Saved", "history": {"currentId": "a"}}, folder_id=None)
        result = method(object(), "member-1", form, compatibility_saved_work=True)
        self.assertEqual(result.user_id, "member-1")
        self.assertEqual(result.meta, {"_mdk_private_saved_work": {"version": 1, "last_read_at": None}})
        self.assertEqual(result.chat, form.chat)
        self.assertEqual(db.commits, 1)

        db.added = None
        db.commits = 0
        unmarked = method(object(), "member-1", form)
        self.assertEqual(unmarked.meta, {})
        self.assertEqual(db.added.meta, {})

    def test_read_marker_updates_only_owned_marked_row_without_touching_updated_at(self):
        marker = {"_mdk_private_saved_work": {"version": 1, "last_read_at": None}, "legacy": {"x": 1}}
        row = SimpleNamespace(id="chat-1", user_id="member-1", meta=copy.deepcopy(marker), updated_at=77)
        db = _DB(row)
        ns = {"get_db": lambda: db, "Chat": object(), "_updated_saved_work_read_meta": self.helpers["_updated_saved_work_read_meta"], "time": SimpleNamespace(time=lambda: 123)}
        method = _method(MODEL_PATH, "ChatTable", "mark_private_saved_chat_read", ns)
        self.assertTrue(method(object(), "chat-1", "member-1"))
        self.assertEqual(db.filters, {"id": "chat-1", "user_id": "member-1"})
        self.assertEqual(row.updated_at, 77)
        self.assertEqual(row.meta["legacy"], {"x": 1})
        self.assertEqual(self.helpers["_saved_work_last_read"](row.meta), 123)
        self.assertEqual(db.commits, 1)

    def test_read_marker_survives_json_roundtrip_for_later_owner_list(self):
        meta = self.helpers["_new_saved_work_meta"]({"unrelated": "keep"})
        updated = self.helpers["_updated_saved_work_read_meta"](meta, 123456)
        reopened_meta = json.loads(json.dumps(updated))
        self.assertEqual(self.helpers["_saved_work_last_read"](reopened_meta), 123456)
        self.assertEqual(reopened_meta["unrelated"], "keep")
        self.assertEqual(self.helpers["_public_chat_meta"](reopened_meta), {"unrelated": "keep"})

    def test_owner_list_projects_persisted_read_time_and_current_defaults(self):
        class Column:
            def __eq__(self, other): return ("eq", other)
            def desc(self): return "updated_at DESC"
        class Chat:
            id = title = updated_at = created_at = meta = archived = Column()
            folder_id = pinned = Column()
        marker = self.helpers["_new_saved_work_meta"]({"keep": "yes"})
        marker = self.helpers["_updated_saved_work_read_meta"](marker, 123456)
        db = _DB()
        db.rows = [("chat-1", "Saved", 20, 10, marker, False)]
        ns = {
            "get_db": lambda: db, "Chat": Chat,
            "PrivateChatTitleIdResponse": SimpleNamespace(model_validate=lambda value: value),
            "_saved_work_last_read": self.helpers["_saved_work_last_read"],
            "or_": lambda *values: values,
        }
        method = _method(MODEL_PATH, "ChatTable", "get_chat_title_id_list_by_user_id", ns)
        result = method(object(), "member-1", False, None, None)
        self.assertEqual(result, [{"id": "chat-1", "title": "Saved", "updated_at": 20,
                                  "created_at": 10, "last_read_at": 123456, "snippet": None,
                                  "active": False, "archived": False}])

    def test_owner_mismatch_and_unmarked_collision_do_not_write_read_state(self):
        row = SimpleNamespace(id="chat-1", user_id="owner", meta={"_mdk_private_saved_work": {"opaque": True}}, updated_at=77)
        db = _DB(row)
        ns = {"get_db": lambda: db, "Chat": object(), "_updated_saved_work_read_meta": self.helpers["_updated_saved_work_read_meta"], "time": SimpleNamespace(time=lambda: 123)}
        method = _method(MODEL_PATH, "ChatTable", "mark_private_saved_chat_read", ns)
        self.assertFalse(method(object(), "chat-1", "member-1"))
        self.assertEqual(row.meta, {"_mdk_private_saved_work": {"opaque": True}})
        self.assertEqual(row.updated_at, 77)
        self.assertEqual(db.commits, 0)

    def test_response_projection_hides_only_internal_marker_and_never_mutates_chat(self):
        project = _response_helper()
        original = {"history": {"currentId": "chosen", "messages": {}}, "messages": [], "title": "Saved"}
        model = SimpleNamespace(
            chat=original, meta={"tags": ["x"], "_mdk_private_saved_work": {"version": 1, "last_read_at": 100}},
            model_dump=lambda: {"id": "chat-1", "user_id": "member-1", "title": "Saved", "chat": original,
                                "created_at": 1, "updated_at": 2, "meta": {"tags": ["x"], "_mdk_private_saved_work": {"version": 1, "last_read_at": 100}},
                                "archived": False, "pinned": False, "folder_id": None},
        )
        response = project(model, self.actor, mark_continue=True)
        self.assertEqual(response["current_message_id"], "chosen")
        self.assertEqual(response["chat"]["share_mode"], "continue")
        self.assertEqual(response["meta"], {"tags": ["x"]})
        self.assertEqual(response["variables"], {})
        self.assertIsNone(response["tasks"])
        self.assertEqual(original, {"history": {"currentId": "chosen", "messages": {}}, "messages": [], "title": "Saved"})
        collision = copy.deepcopy(model)
        collision.model_dump = lambda: {**model.model_dump(), "meta": {"_mdk_private_saved_work": {"opaque": True}}}
        fallback = project(collision, self.actor, mark_continue=True)
        self.assertEqual(fallback["meta"], {"_mdk_private_saved_work": {"opaque": True}})
        self.assertNotIn("share_mode", fallback["chat"])

    def test_read_projection_refreshes_current_names_and_deduplicates_other_authors(self):
        project = self.helpers["_project_private_chat_authors"]
        current_user = SimpleNamespace(id="member-1", name="Renamed member")
        source = {
            "history": {"messages": {
                "q": {"role": "user", "user_id": "member-1", "user": {"id": "member-1", "name": "Old name"}},
                "q2": {"role": "user", "user_id": "other-1", "user": {"id": "other-1", "name": "Claimed name"}},
            }},
            "messages": [
                {"role": "user", "user_id": "member-1", "user": {"id": "member-1", "name": "Old name"}},
                {"role": "user", "user_id": "other-1", "user": {"id": "other-1", "name": "Claimed name"}},
            ],
        }
        lookups = []
        def lookup(user_id):
            lookups.append(user_id)
            return SimpleNamespace(id=user_id, name="Resolved other")
        response = project(source, current_user, lookup)
        self.assertEqual(lookups, ["other-1"])
        self.assertEqual(response["history"]["messages"]["q"]["user"]["name"], "Renamed member")
        self.assertEqual(response["messages"][0]["user"]["name"], "Renamed member")
        self.assertEqual(response["history"]["messages"]["q2"]["user"]["name"], "Resolved other")
        self.assertEqual(response["messages"][1]["user"]["name"], "Resolved other")
        self.assertEqual(source["messages"][0]["user"]["name"], "Old name")

    def test_actual_update_route_denies_before_mutation(self):
        class Denied(Exception):
            def __init__(self, **kwargs): self.kwargs = kwargs
        effects = []
        namespace = {
            "Chats": SimpleNamespace(get_chat_by_id_and_user_id=lambda *_: None,
                                      update_chat_by_id=lambda *_: effects.append("write")),
            "_saved_work_marker": self.helpers["_saved_work_marker"],
            "_normalize_private_chat_authors": lambda *_: effects.append("normalize"),
            "_saved_chat_response": lambda *_: effects.append("response"),
            "HTTPException": Denied, "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="denied"),
        }
        route = _function(ROUTE_PATH, "update_chat_by_id", namespace)
        with self.assertRaises(Denied) as raised:
            asyncio.run(route("private-chat", SimpleNamespace(chat={"title": "bad"}), self.actor))
        self.assertEqual(raised.exception.kwargs["status_code"], 401)
        self.assertEqual(effects, [])

    def test_actual_create_route_stores_actor_attribution_and_projects_current_fields(self):
        original = {"title": "Saved", "history": {"currentId": "a", "messages": {
            "a": {"id": "a", "role": "user", "user_id": "claimed"}}},
            "messages": [{"id": "a", "role": "user", "user_id": "claimed"}]}
        persisted = {}
        class FakeModel:
            def __init__(self, chat):
                self.chat = chat
                self.meta = {"_mdk_private_saved_work": {"version": 1, "last_read_at": None}}
                self.folder_id = None
            def model_dump(self):
                return {"id": "chat-1", "user_id": "member-1", "title": self.chat["title"],
                        "chat": self.chat, "created_at": 1, "updated_at": 1, "archived": False,
                        "pinned": False, "meta": self.meta, "folder_id": self.folder_id}
        def insert(user_id, form, compatibility_saved_work=False):
            persisted["user_id"] = user_id
            persisted["form"] = form
            persisted["compatibility_saved_work"] = compatibility_saved_work
            persisted["row"] = FakeModel(form.chat)
            return persisted["row"]
        ns = {
            "_normalize_private_chat_authors": self.helpers["_normalize_private_chat_authors"],
            "_saved_chat_response": _response_helper(),
            "_require_supported_saved_chat_fields": lambda _form: None,
            "PrivateChatForm": lambda **kwargs: SimpleNamespace(**kwargs),
            "Chats": SimpleNamespace(insert_new_chat=insert),
            "log": SimpleNamespace(exception=lambda *_args: None),
            "HTTPException": RuntimeError,
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "error"),
            "status": SimpleNamespace(HTTP_400_BAD_REQUEST=400),
        }
        route = _function(ROUTE_PATH, "create_new_chat", ns)
        response = asyncio.run(route(SimpleNamespace(chat=original, variables={}, folder_id=None), self.actor))
        self.assertEqual(persisted["user_id"], "member-1")
        self.assertTrue(persisted["compatibility_saved_work"])
        stored = persisted["row"].chat
        self.assertEqual(stored["history"]["messages"]["a"]["user"], {"id": "member-1", "name": "Synthetic member"})
        self.assertEqual(stored["messages"][0]["user_id"], "member-1")
        self.assertEqual(response["current_message_id"], "a")
        self.assertEqual(response["meta"], {})
        self.assertEqual(original["history"]["messages"]["a"]["user_id"], "claimed")

    def test_unmeasured_variables_and_folder_inputs_fail_before_storage(self):
        class ApiError(Exception):
            def __init__(self, status_code, detail):
                self.status_code, self.detail = status_code, detail
        namespace = {"HTTPException": ApiError, "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422)}
        validate = _function(ROUTE_PATH, "_require_supported_saved_chat_fields", namespace)
        for form in (SimpleNamespace(variables={"key": "value"}, folder_id=None),
                     SimpleNamespace(variables={}, folder_id="folder-1")):
            with self.subTest(form=form), self.assertRaises(ApiError) as raised:
                validate(form)
            self.assertEqual(raised.exception.status_code, 422)
        validate(SimpleNamespace(variables=None, folder_id=None))

        class ApiError2(Exception):
            def __init__(self, **kwargs): self.kwargs = kwargs
        effects = []
        route_ns = {
            "_require_supported_saved_chat_fields": validate,
            "Chats": SimpleNamespace(insert_new_chat=lambda *_, **__: effects.append("insert")),
            "HTTPException": ApiError2,
            "status": SimpleNamespace(HTTP_422_UNPROCESSABLE_ENTITY=422, HTTP_400_BAD_REQUEST=400),
            "ERROR_MESSAGES": SimpleNamespace(DEFAULT=lambda: "error"),
            "log": SimpleNamespace(exception=lambda *_: None),
        }
        create = _function(ROUTE_PATH, "create_new_chat", route_ns)
        with self.assertRaises(ApiError):
            asyncio.run(create(SimpleNamespace(chat={"title": "Saved"}, variables={"private": "data"}, folder_id=None), self.actor))
        self.assertEqual(effects, [])

    def test_actual_owner_read_marks_only_after_owner_lookup_and_projects_continue(self):
        stored_chat = {"title": "Saved", "history": {"currentId": "a", "messages": {
            "a": {"id": "a", "role": "user", "user_id": "member-1", "user": {"id": "member-1", "name": "Previous name"}}}},
            "messages": [{"id": "a", "role": "user", "user_id": "member-1", "user": {"id": "member-1", "name": "Previous name"}}]}
        model = SimpleNamespace(
            chat=stored_chat,
            model_dump=lambda: {"id": "chat-1", "user_id": "member-1", "title": "Saved", "chat": stored_chat,
                                "created_at": 1, "updated_at": 2, "meta": {"_mdk_private_saved_work": {"version": 1, "last_read_at": None}},
                                "archived": False, "pinned": False, "folder_id": None},
        )
        effects = []
        ns = {
            "Chats": SimpleNamespace(get_chat_by_id_and_user_id=lambda chat_id, user_id: model,
                                      mark_private_saved_chat_read=lambda chat_id, user_id: effects.append(("read", chat_id, user_id))),
            "_saved_chat_response": _response_helper(), "Users": SimpleNamespace(get_user_by_id=lambda _user_id: None),
            "PrivateChatResponse": lambda **kwargs: kwargs,
            "HTTPException": RuntimeError, "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(NOT_FOUND="not found"),
        }
        route = _function(ROUTE_PATH, "get_chat_by_id", ns)
        renamed_actor = SimpleNamespace(id="member-1", name="Current synthetic name", role="user")
        response = asyncio.run(route("chat-1", renamed_actor))
        self.assertEqual(effects, [("read", "chat-1", "member-1")])
        self.assertEqual(response["chat"]["share_mode"], "continue")
        self.assertEqual(response["chat"]["history"]["messages"]["a"]["user"]["name"], "Current synthetic name")
        self.assertEqual(response["chat"]["messages"][0]["user"]["name"], "Current synthetic name")
        self.assertNotIn("share_mode", stored_chat)
        self.assertEqual(stored_chat["messages"][0]["user"]["name"], "Previous name")

    def test_actual_marked_update_rewrites_content_claims_without_changing_owner(self):
        stored = {"title": "Saved", "history": {"currentId": "a", "messages": {
            "a": {"id": "a", "role": "user", "user_id": "claimed"}}},
            "messages": [{"id": "a", "role": "user", "user_id": "claimed"}]}
        marker = {"_mdk_private_saved_work": {"version": 1, "last_read_at": None}}
        existing = SimpleNamespace(chat=stored, meta=marker)
        captured = {}
        class Updated:
            def __init__(self, chat):
                self.chat, self.meta = chat, marker
            def model_dump(self):
                return {"id": "chat-1", "user_id": "member-1", "title": self.chat["title"],
                        "chat": self.chat, "created_at": 1, "updated_at": 3, "meta": self.meta,
                        "archived": False, "pinned": False, "folder_id": None}
        def save(chat_id, chat_data):
            captured["id"], captured["chat"] = chat_id, chat_data
            return Updated(chat_data)
        ns = {
            "Chats": SimpleNamespace(get_chat_by_id_and_user_id=lambda *_: existing, update_chat_by_id=save),
            "_saved_work_marker": self.helpers["_saved_work_marker"],
            "_normalize_private_chat_authors": self.helpers["_normalize_private_chat_authors"],
            "_require_supported_saved_chat_fields": lambda _form: None,
            "_saved_chat_response": _response_helper(),
            "HTTPException": RuntimeError, "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(ACCESS_PROHIBITED="denied"),
        }
        route = _function(ROUTE_PATH, "update_chat_by_id", ns)
        response = asyncio.run(route("chat-1", SimpleNamespace(chat={"title": "Saved"}), self.actor))
        self.assertEqual(captured["id"], "chat-1")
        self.assertEqual(response["user_id"], "member-1")
        self.assertEqual(captured["chat"]["history"]["messages"]["a"]["user_id"], "member-1")
        self.assertEqual(captured["chat"]["messages"][0]["user"]["name"], "Synthetic member")
        self.assertEqual(stored["history"]["messages"]["a"]["user_id"], "claimed")

    def test_actual_get_route_denies_before_read_marker_or_projection(self):
        class Denied(Exception):
            def __init__(self, **kwargs): self.kwargs = kwargs
        effects = []
        ns = {
            "Chats": SimpleNamespace(get_chat_by_id_and_user_id=lambda *_: None,
                                      mark_private_saved_chat_read=lambda *_: effects.append("read")),
            "_saved_chat_response": lambda *_args, **_kwargs: effects.append("projection"),
            "HTTPException": Denied, "status": SimpleNamespace(HTTP_401_UNAUTHORIZED=401),
            "ERROR_MESSAGES": SimpleNamespace(NOT_FOUND="not found"),
        }
        route = _function(ROUTE_PATH, "get_chat_by_id", ns)
        with self.assertRaises(Denied) as raised:
            asyncio.run(route("private-chat", self.actor))
        self.assertEqual(raised.exception.kwargs["status_code"], 401)
        self.assertEqual(effects, [])

    def test_current_message_projection_handles_malformed_optional_history(self):
        current = self.helpers["_chat_current_message_id"]
        self.assertEqual(current({"history": {"currentId": "x"}}), "x")
        for value in (None, [], {"history": []}, {"history": {"currentId": 3}}):
            self.assertIsNone(current(value))

    def test_extended_response_fields_are_private_route_scoped(self):
        model_tree = ast.parse(MODEL_PATH.read_text(encoding="utf-8"))
        classes = {node.name: node for node in model_tree.body if isinstance(node, ast.ClassDef)}
        def fields(class_name):
            return {node.target.id for node in classes[class_name].body
                    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
        self.assertFalse({"variables", "tasks", "summary", "current_message_id", "context_usage"} & fields("ChatResponse"))
        self.assertTrue({"variables", "tasks", "summary", "current_message_id", "context_usage"} <= fields("PrivateChatResponse"))
        self.assertFalse({"last_read_at", "active", "snippet"} & fields("ChatTitleIdResponse"))
        self.assertTrue({"last_read_at", "active", "snippet"} <= fields("PrivateChatTitleIdResponse"))
        self.assertFalse({"variables", "folder_id"} & fields("ChatForm"))
        self.assertTrue({"variables", "folder_id"} <= fields("PrivateChatForm"))
        route_tree = ast.parse(ROUTE_PATH.read_text(encoding="utf-8"))
        routes = {node.name: node for node in route_tree.body if isinstance(node, ast.AsyncFunctionDef)}
        def response_model(route):
            for decorator in routes[route].decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in ("get", "post"):
                    for kw in decorator.keywords:
                        if kw.arg == "response_model":
                            return ast.unparse(kw.value)
        self.assertIn("PrivateChatResponse", response_model("get_chat_by_id"))
        self.assertIn("PrivateChatResponse", response_model("update_chat_by_id"))
        self.assertIn("ChatResponse", response_model("get_shared_chat_by_id"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
