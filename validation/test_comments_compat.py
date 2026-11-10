# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused tests for the bounded collaborative-comments compatibility slice."""

from contextlib import contextmanager
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys

from fastapi import FastAPI
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import declarative_base, sessionmaker


ROOT = Path(__file__).resolve().parents[1]


def _package(monkeypatch, name):
    module = ModuleType(name)
    module.__path__ = []
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _module(monkeypatch, name, **values):
    module = ModuleType(name)
    for key, value in values.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _load(monkeypatch, name, relative):
    spec = spec_from_file_location(name, ROOT / relative)
    module = module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _comments_model(monkeypatch):
    _package(monkeypatch, "open_webui")
    _package(monkeypatch, "open_webui.internal")
    _package(monkeypatch, "open_webui.models")
    base = declarative_base()
    engine = create_engine("sqlite:///:memory:")
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    _module(monkeypatch, "open_webui.internal.db", Base=base, get_db=get_db)
    module = _load(monkeypatch, "open_webui.models.comments", "backend/open_webui/models/comments.py")
    base.metadata.create_all(engine)
    return module


def _form(**values):
    defaults = {
        "content": "Synthetic comment",
        "resource_type": "chat",
        "resource_id": "chat-1",
        "target_type": None,
        "target_id": None,
        "anchor": None,
        "temp_id": None,
        "reply_to_id": None,
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_comment_lifecycle_and_soft_delete(monkeypatch):
    module = _comments_model(monkeypatch)
    root = module.Comments.create("owner", _form())
    assert root.content == "Synthetic comment"
    rows, total, cursor = module.Comments.list_roots("chat", "chat-1", limit=20)
    assert [item.id for item in rows] == [root.id]
    assert total == 1 and cursor is None

    updated, error = module.Comments.update(root.id, "Edited", str(root.updated_at))
    assert error is None and updated.content == "Edited" and updated.updated_at > root.updated_at
    stale, error = module.Comments.update(root.id, "Stale", str(root.updated_at))
    assert error == "conflict" and stale.content == "Edited"

    deleted, error = module.Comments.soft_delete(root.id, str(updated.updated_at))
    assert error is None and deleted.content == "" and deleted.user_id is None
    assert deleted.deleted_at == deleted.updated_at


def test_replies_reactions_and_pagination_are_bounded(monkeypatch):
    module = _comments_model(monkeypatch)
    first = module.Comments.create("owner", _form(content="First"))
    second = module.Comments.create("owner", _form(content="Second"))
    page, total, cursor = module.Comments.list_roots("chat", "chat-1", limit=1)
    assert [item.id for item in page] == [first.id]
    assert total == 2 and cursor == f"{first.created_at}:{first.id}"
    page, total, cursor = module.Comments.list_roots("chat", "chat-1", cursor=cursor, limit=1)
    assert [item.id for item in page] == [second.id]
    assert total == 2 and cursor is None

    reply = module.Comments.create("owner", _form(content="Reply"), parent=first)
    replies, cursor = module.Comments.replies(first.id)
    assert [item.id for item in replies] == [reply.id] and cursor is None
    assert module.Comments.reply_summary(first.id) == (1, reply.created_at)

    assert module.Comments.add_reaction(first.id, "owner", "like") is True
    assert module.Comments.add_reaction(first.id, "owner", "like") is True
    assert module.Comments.add_reaction(first.id, "admin", "like") is True
    assert module.Comments.reactions(first.id) == [
        {"user_id": "owner", "name": "like"},
        {"user_id": "admin", "name": "like"},
    ]
    assert module.Comments.remove_reaction(first.id, "owner", "missing") is True
    assert module.Comments.remove_reaction(first.id, "owner", "like") is True
    assert module.Comments.reactions(first.id) == [{"user_id": "admin", "name": "like"}]


def test_temp_identity_and_anchor_projection(monkeypatch):
    module = _comments_model(monkeypatch)
    anchor = SimpleNamespace(model_dump=lambda: {"quote": "Synthetic", "start": 0, "end": 9})
    comment = module.Comments.create(
        "owner",
        _form(
            temp_id="00000000-0000-4000-8000-000000000052",
            target_type="chat_message",
            target_id="message-1",
            anchor=anchor,
        ),
    )
    assert comment.id == "00000000-0000-4000-8000-000000000052"
    assert comment.meta == {"anchor": {"quote": "Synthetic", "start": 0, "end": 9}}
    anchored, total, _ = module.Comments.list_roots("chat", "chat-1", anchored=True)
    assert [item.id for item in anchored] == [comment.id] and total == 1


def test_public_forms_and_route_inventory(monkeypatch):
    comments_model = _comments_model(monkeypatch)
    _package(monkeypatch, "open_webui.routers")
    _package(monkeypatch, "open_webui.utils")

    class Table:
        def __getattr__(self, _name):
            return lambda *_args, **_kwargs: None

    for name, attribute in (
        ("chats", "Chats"), ("knowledge", "Knowledges"), ("models", "Models"),
        ("prompts", "Prompts"), ("skills", "Skills"), ("tools", "Tools"),
        ("users", "Users"),
    ):
        _module(monkeypatch, f"open_webui.models.{name}", **{attribute: Table()})
    monkeypatch.setitem(sys.modules, "open_webui.models.comments", comments_model)
    _module(monkeypatch, "open_webui.utils.access_control", has_access=lambda *_args, **_kwargs: False)
    _module(monkeypatch, "open_webui.utils.auth", get_verified_user=lambda: None)
    module = _load(monkeypatch, "open_webui.routers.comments", "backend/open_webui/routers/comments.py")

    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/comments")
    paths = app.openapi()["paths"]
    assert set(paths) == {
        "/api/v1/comments/",
        "/api/v1/comments/context",
        "/api/v1/comments/mentions",
        "/api/v1/comments/{id}",
        "/api/v1/comments/{id}/replies",
        "/api/v1/comments/{id}/reactions",
    }
    operations = {(method.upper(), path) for path, values in paths.items() for method in values}
    assert operations == {
        ("GET", "/api/v1/comments/"), ("POST", "/api/v1/comments/"),
        ("GET", "/api/v1/comments/context"), ("GET", "/api/v1/comments/mentions"),
        ("GET", "/api/v1/comments/{id}"), ("PATCH", "/api/v1/comments/{id}"),
        ("DELETE", "/api/v1/comments/{id}"),
        ("GET", "/api/v1/comments/{id}/replies"),
        ("POST", "/api/v1/comments/{id}/replies"),
        ("PUT", "/api/v1/comments/{id}/reactions"),
        ("DELETE", "/api/v1/comments/{id}/reactions"),
    }
    assert module.CommentForm.model_json_schema()["additionalProperties"] is False
    assert module.CommentUpdateForm.model_json_schema()["additionalProperties"] is False


def test_comment_migration_upgrades_and_downgrades_sqlite(monkeypatch):
    module = _load(
        monkeypatch,
        "round52_comment_migration",
        "backend/open_webui/migrations/versions/f20b4c5d6e7f_add_comments.py",
    )
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = inspect(connection)
        assert {"comment", "comment_reaction"}.issubset(inspector.get_table_names())
        assert {"id", "resource_type", "resource_id", "meta", "deleted_at"}.issubset(
            {column["name"] for column in inspector.get_columns("comment")}
        )
        unique = inspector.get_unique_constraints("comment_reaction")
        assert any(set(item["column_names"]) == {"comment_id", "user_id", "name"} for item in unique)
        module.downgrade()
        assert not {"comment", "comment_reaction"}.intersection(inspect(connection).get_table_names())
