# SPDX-License-Identifier: BSD-3-Clause
"""Execute the checked-in legacy group lookup method with an isolated query stub."""

import ast
import pathlib
import unittest
from types import SimpleNamespace


SOURCE = pathlib.Path(__file__).resolve().parents[1] / "models" / "groups.py"


def load_lookup_method():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    groups = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "GroupTable")
    method = next(
        node for node in groups.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_groups_by_member_id"
    )

    class Column:
        @staticmethod
        def desc():
            return "updated_at DESC"

    class Model:
        @staticmethod
        def model_validate(value):
            return value

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def order_by(self, _ordering):
            return self

        def filter(self, _predicate):
            return self

        def all(self):
            return self.rows

    class Database:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def query(self, _model):
            return Query(self.rows)

    class GroupClass:
        updated_at = Column()
        user_ids = "user_ids"

    namespace = {
        "Group": GroupClass,
        "GroupModel": Model,
        "func": SimpleNamespace(json_array_length=lambda _column: 1),
        "get_db": lambda: Database(namespace["rows"]),
        "rows": [],
    }
    module = ast.Module(body=[method], type_ignores=[])
    exec(compile(module, str(SOURCE), "exec"), namespace)
    return namespace["get_groups_by_member_id"], namespace


class ExactLegacyGroupMembershipTests(unittest.TestCase):
    def test_prefix_case_wildcard_and_json_delimiter_ids_match_only_exact_members(self):
        lookup, namespace = load_lookup_method()
        namespace["rows"] = [
            SimpleNamespace(id="prefix", user_ids=["user-10"]),
            SimpleNamespace(id="case", user_ids=["User-A"]),
            SimpleNamespace(id="percent", user_ids=["user%"]),
            SimpleNamespace(id="percent-false-positive", user_ids=["userZ"]),
            SimpleNamespace(id="underscore", user_ids=["a_b"]),
            SimpleNamespace(id="underscore-false-positive", user_ids=["userX-other"]),
            SimpleNamespace(id="delimiter", user_ids=['user"\\quoted']),
            SimpleNamespace(id="comma", user_ids=["user,a"]),
            SimpleNamespace(id="other", user_ids=["unrelated"]),
        ]
        for user_id, expected in (
            ("user-1", []),
            ("user-10", ["prefix"]),
            ("user-a", []),
            ("User-A", ["case"]),
            ("user", []),
            ("user%", ["percent"]),
            ("user_%", []),
            ("userZ", ["percent-false-positive"]),
            ("aXb", []),
            ("a_b", ["underscore"]),
            ("userX-other", ["underscore-false-positive"]),
            ('user"\\quoted', ["delimiter"]),
            ("user,a", ["comma"]),
        ):
            with self.subTest(user_id=user_id):
                self.assertEqual(
                    [group.id for group in lookup(None, user_id)], expected
                )

    def test_string_or_object_blobs_are_not_treated_as_membership_arrays(self):
        lookup, namespace = load_lookup_method()
        namespace["rows"] = [
            SimpleNamespace(id="string", user_ids='["user-a"]'),
            SimpleNamespace(id="object", user_ids={"user-a": True}),
            SimpleNamespace(id="mixed", user_ids=[None, 4, "user-a"]),
        ]
        self.assertEqual([group.id for group in lookup(None, "user-a")], ["mixed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
