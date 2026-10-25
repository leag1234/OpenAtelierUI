# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for the measured flat-group API projection and mutations."""

import ast
import importlib.util
import pathlib
import unittest
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPAT_PATH = ROOT / "utils" / "group_compat.py"
COMPAT_SPEC = importlib.util.spec_from_file_location("group_compat_under_test", COMPAT_PATH)
COMPAT_MODULE = importlib.util.module_from_spec(COMPAT_SPEC)
COMPAT_SPEC.loader.exec_module(COMPAT_MODULE)
project_group_members = COMPAT_MODULE.project_group_members
project_group_response = COMPAT_MODULE.project_group_response


def _extract_async_function(path, function_name, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == function_name
    )
    fn.decorator_list = []
    fn.returns = None
    fn.type_comment = None
    for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs):
        arg.annotation = None
    fn.args.vararg = None
    fn.args.kwarg = None
    fn.args.defaults = []
    fn.args.kw_defaults = [None] * len(fn.args.kwonlyargs)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[function_name]


class _Database:
    def __init__(self, group, users, fail_commit=False):
        self.group = group
        self.users = users
        self.fail_commit = fail_commit
        self.statements = []
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        self.before = list(group.user_ids) if group else None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement):
        self.statements.append(str(statement))

    def query(self, model):
        return _Query(self, model)

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("injected commit failure")

    def refresh(self, _group):
        pass

    def rollback(self):
        if self.group is not None:
            self.group.user_ids = list(self.before)


class _Query:
    def __init__(self, db, model):
        self.db = db
        self.model = model

    def filter_by(self, **filters):
        self.filters = filters
        return self

    def filter(self, _expression):
        return self

    def first(self):
        return self.db.group

    def all(self):
        if self.model is _FakeUser.id:
            return [(user_id,) for user_id in self.db.users]
        return []

    def with_for_update(self):
        return self


class _FakeUser:
    class _Id:
        @staticmethod
        def in_(_values):
            return "user_id IN (...)"

    id = _Id()


def _load_mutator(db):
    namespace = {
        "Literal": __import__("typing").Literal,
        "time": SimpleNamespace(time=lambda: 200),
        "get_db": lambda: db,
        "Group": object(),
        "GroupModel": SimpleNamespace(model_validate=lambda group: group),
        "User": _FakeUser,
        "text": lambda value: value,
    }
    # The checked-in method uses a local import to avoid the models.users ↔
    # models.groups import cycle. Replace only that import in the extracted test.
    tree = ast.parse((ROOT / "models" / "groups.py").read_text(encoding="utf-8"))
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GroupTable")
    fn = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == "mutate_group_user_ids")
    fn.body = [n for n in fn.body if not (isinstance(n, ast.ImportFrom) and n.module == "open_webui.models.users")]
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(ROOT / "models" / "groups.py"), "exec"), namespace)
    return namespace["mutate_group_user_ids"]


class FlatGroupApiCompatibilityTests(unittest.TestCase):
    def test_detail_projection_excludes_membership_and_counts_exact_unique_ids(self):
        group = {
            "id": "g1", "user_id": "owner", "name": "Workshop", "description": "members",
            "permissions": {}, "data": {"config": {"share": "members"}}, "meta": None,
            "user_ids": ["u2", "u1", "u2"], "created_at": 1, "updated_at": 2,
        }
        result = project_group_response(group)
        self.assertEqual(result["member_count"], 2)
        self.assertIsNone(result["parent_group_id"])
        self.assertNotIn("user_ids", result)

    def test_members_filter_search_paginate_and_suppress_private_presence(self):
        group = SimpleNamespace(user_ids=["u3", "u1", "u2"])
        users = [
            SimpleNamespace(id="u1", name="Béatrice", email="b@example.test", role="user"),
            SimpleNamespace(id="u2", name="Alice", email="a@example.test", role="user"),
            SimpleNamespace(id="u3", name="Cleo", email="c@example.test", role="admin", last_active_at=99, settings={"secret": True}),
        ]
        result = project_group_members(group, users, query="EXAMPLE", page=1)
        self.assertEqual(result["total"], 3)
        self.assertEqual([item["id"] for item in result["items"]], ["u2", "u1", "u3"])
        self.assertIsNone(result["items"][2]["last_active_at"])
        self.assertNotIn("settings", result["items"][2])
        self.assertEqual(project_group_members(group, users, "inherited")["total"], 0)

    def test_members_reject_bad_filter_and_page(self):
        for kwargs in ({"membership": "parent"}, {"page": 0}, {"page": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                project_group_members({"user_ids": []}, [], **kwargs)

    def test_measured_member_page_size_and_name_order(self):
        users = [
            SimpleNamespace(id=f"u{index:02d}", name=f"Profile {index:02d}", email=f"{index}@example.test", role="user")
            for index in reversed(range(35))
        ]
        group = {"user_ids": [user.id for user in users]}
        first = project_group_members(group, users)
        second = project_group_members(group, users, page=2)
        self.assertEqual((first["total"], len(first["items"])), (35, 30))
        self.assertEqual([item["id"] for item in first["items"]], [f"u{index:02d}" for index in range(30)])
        self.assertEqual([item["id"] for item in second["items"]], [f"u{index:02d}" for index in range(30, 35)])

    def test_add_remove_deduplicates_and_only_updates_when_membership_changes(self):
        group = SimpleNamespace(id="g", user_ids=["u1", "u1"], updated_at=10)
        db = _Database(group, {"u1", "u2"})
        mutate = _load_mutator(db)
        result = mutate(None, "g", ["u2", "u2"], "add")
        self.assertEqual(result.user_ids, ["u1", "u2"])
        self.assertEqual(result.updated_at, 200)
        self.assertEqual(db.statements, ["BEGIN IMMEDIATE"])

        group.updated_at = 200
        noop_db = _Database(group, {"u2"})
        noop = _load_mutator(noop_db)(None, "g", ["u2"], "add")
        self.assertEqual(noop.updated_at, 200)
        self.assertEqual(noop.user_ids, ["u1", "u2"])

        remove_db = _Database(group, {"u1"})
        removed = _load_mutator(remove_db)(None, "g", ["u1"], "remove")
        self.assertEqual(removed.user_ids, ["u2"])

    def test_commit_failure_rolls_back_membership(self):
        group = SimpleNamespace(id="g", user_ids=["u1"], updated_at=10)
        db = _Database(group, {"u2"}, fail_commit=True)
        with self.assertRaisesRegex(RuntimeError, "injected"):
            _load_mutator(db)(None, "g", ["u2"], "add")
        self.assertEqual(group.user_ids, ["u1"])

    def test_route_contract_keeps_admin_dependency_and_required_body(self):
        path = ROOT / "routers" / "groups.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        routes = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
        }
        for name in ("get_group_members", "add_group_users", "remove_group_users"):
            with self.subTest(route=name):
                route = routes[name]
                self.assertIn("get_admin_user", ast.unparse(route.args))
                if name != "get_group_members":
                    self.assertIn("GroupUserIdsForm", ast.unparse(route.args))
        self.assertEqual(
            routes["get_group_by_id"].decorator_list[0].args[0].value,
            "/id/{id}",
        )

    def test_actual_mutation_route_helper_filters_and_reports_missing_group(self):
        class ApiError(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        group = SimpleNamespace(id="g", user_ids=["u1"], updated_at=10)
        namespace = {
            "Users": SimpleNamespace(get_valid_user_ids=lambda ids: [x for x in ids if x != "unknown"]),
            "Groups": SimpleNamespace(
                mutate_group_user_ids=lambda group_id, ids, action: (
                    group if group_id == "g" else None
                )
            ),
            "project_group_response": lambda value: {"id": value.id},
            "HTTPException": ApiError,
            "log": SimpleNamespace(exception=lambda *_args: None),
        }
        helper = _extract_async_function(ROOT / "routers" / "groups.py", "_mutate_group_membership", namespace)
        response = __import__("asyncio").run(
            helper("g", SimpleNamespace(user_ids=["u1", "unknown", "u1"]), "add")
        )
        self.assertEqual(response, {"id": "g"})
        with self.assertRaises(ApiError) as raised:
            __import__("asyncio").run(
                helper("missing", SimpleNamespace(user_ids=["u1"]), "remove")
            )
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Group not found.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
