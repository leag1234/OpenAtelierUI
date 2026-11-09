"""Focused checks for recent tool specs and activation compatibility."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODEL_SOURCE = ROOT / "backend/open_webui/models/tools.py"
ROUTER_SOURCE = ROOT / "backend/open_webui/routers/tools.py"
MIGRATION_SOURCE = (
    ROOT
    / "backend/open_webui/migrations/versions/f19a3b4c5d6e_add_tool_activation.py"
)


class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _load_routes(names, namespace):
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
    if {node.name for node in nodes} != set(names):
        raise AssertionError("Missing route source")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTER_SOURCE), "exec"), namespace)


class ToolSpecsToggleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resolve_result = None
        self.tool = None
        self.toggled = None
        self.toggle_calls = []

        async def get_tool_servers_data(_connections):
            return []

        async def refresh(_connections, servers):
            return servers

        def resolve(_identifier, _connections, _servers, _user):
            return self.resolve_result

        class Store:
            @staticmethod
            def get_tool_by_id(_identifier):
                return self.tool

            @staticmethod
            def toggle_tool_by_id(identifier):
                self.toggle_calls.append(identifier)
                return self.toggled

        class ResponseModel:
            pass

        namespace = {
            "Request": object,
            "Depends": lambda dependency: dependency,
            "get_verified_user": lambda: None,
            "get_tool_servers_data": get_tool_servers_data,
            "_refresh_named_tool_server_catalog": refresh,
            "resolve_tool_server_reference": resolve,
            "HTTPException": HTTPException,
            "ToolResponse": ResponseModel,
            "Tools": Store,
            "ERROR_MESSAGES": SimpleNamespace(
                UNAUTHORIZED="401 Unauthorized",
                NOT_FOUND="We could not find what you're looking for :/",
            ),
            "access_grants_for_acl": lambda *_args: [],
            "_tool_current_version": lambda _tool: "synthetic-version",
        }
        _load_routes({"get_tool_specs_by_id", "toggle_tool_by_id"}, namespace)
        self.specs_route = namespace["get_tool_specs_by_id"]
        self.toggle_route = namespace["toggle_tool_by_id"]

    @staticmethod
    def request(servers=None):
        state = SimpleNamespace(
            config=SimpleNamespace(TOOL_SERVER_CONNECTIONS=[]),
            TOOL_SERVERS=[] if servers is None else servers,
        )
        return SimpleNamespace(app=SimpleNamespace(state=state))

    @staticmethod
    def user(identifier="owner", role="user"):
        return SimpleNamespace(id=identifier, role=role)

    @staticmethod
    def record(active=False, owner="owner"):
        values = {
            "id": "synthetic",
            "user_id": owner,
            "name": "Synthetic tool",
            "content": "source",
            "specs": [{"name": "local"}],
            "meta": {},
            "access_control": None,
            "is_active": active,
            "created_at": 1,
            "updated_at": 1,
            "version_id": None,
        }

        class Record(SimpleNamespace):
            def model_dump(self, exclude=None):
                result = dict(self.__dict__)
                for key in exclude or ():
                    result.pop(key, None)
                return result

        return Record(**values)

    async def test_specs_projects_only_server_name_and_description(self):
        server = {
            "specs": [
                {
                    "name": "synthetic_echo",
                    "description": "Return synthetic text.",
                    "parameters": {"type": "object"},
                }
            ]
        }
        self.resolve_result = ({}, server)
        result = await self.specs_route(
            self.request([server]), "server:synthetic", self.user(role="admin")
        )
        self.assertEqual(
            result,
            {
                "specs": [
                    {
                        "name": "synthetic_echo",
                        "description": "Return synthetic text.",
                    }
                ]
            },
        )

    async def test_specs_hides_missing_inaccessible_and_local_tools(self):
        with self.assertRaises(HTTPException) as caught:
            await self.specs_route(
                self.request([{}]), "synthetic", self.user()
            )
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (404, "Tool not found"))

    async def test_toggle_owner_response_and_state_are_bounded(self):
        self.tool = self.record(active=True)
        self.toggled = self.record(active=False)
        result = await self.toggle_route("synthetic", self.user())
        self.assertEqual(self.toggle_calls, ["synthetic"])
        self.assertFalse(result["is_active"])
        self.assertEqual(result["version_id"], "synthetic-version")
        self.assertEqual(result["access_grants"], [])
        self.assertNotIn("content", result)
        self.assertNotIn("specs", result)

    async def test_toggle_foreign_and_missing_contracts(self):
        with self.assertRaises(HTTPException) as missing:
            await self.toggle_route("missing", self.user())
        self.assertEqual(
            (missing.exception.status_code, missing.exception.detail),
            (404, "We could not find what you're looking for :/"),
        )

        self.tool = self.record(owner="owner")
        with self.assertRaises(HTTPException) as foreign:
            await self.toggle_route("synthetic", self.user("other"))
        self.assertEqual((foreign.exception.status_code, foreign.exception.detail), (403, "401 Unauthorized"))
        self.assertEqual(self.toggle_calls, [])

    def test_model_and_migration_preserve_activation(self):
        model_source = MODEL_SOURCE.read_text(encoding="utf-8")
        migration_source = MIGRATION_SOURCE.read_text(encoding="utf-8")
        self.assertIn("is_active = Column(Boolean", model_source)
        self.assertIn("def toggle_tool_by_id", model_source)
        self.assertIn('revision = "f19a3b4c5d6e"', migration_source)
        self.assertIn('down_revision = "f18e2f3a4b5c"', migration_source)
        self.assertIn('op.add_column(', migration_source)


if __name__ == "__main__":
    unittest.main()
