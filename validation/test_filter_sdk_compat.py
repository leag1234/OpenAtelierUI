# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for the bounded direct-Users Filter SDK bridge."""

import ast
import asyncio
import importlib.util
import inspect
import logging
import pathlib
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPAT_SOURCE = ROOT / "backend/open_webui/utils/plugin_user_compat.py"
FILTER_SOURCE = ROOT / "backend/open_webui/utils/filter.py"
PLUGIN_SOURCE = ROOT / "backend/open_webui/utils/plugin.py"


def load_compat():
    spec = importlib.util.spec_from_file_location("isolated_filter_compat", COMPAT_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_filter_dispatch(compat, function_store):
    tree = ast.parse(FILTER_SOURCE.read_text(encoding="utf-8"), filename=str(FILTER_SOURCE))
    dispatch = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "process_filter_functions"
    )
    namespace = {
        "inspect": inspect,
        "log": logging.getLogger("filter-sdk-test"),
        "Functions": function_store,
        "load_function_module_by_id": lambda _id: (_ for _ in ()).throw(
            AssertionError("expected the existing request cache to be used")
        ),
        "set_filter_async_user_lookup_mode": compat.set_filter_async_user_lookup_mode,
        "reset_filter_async_user_lookup_mode": compat.reset_filter_async_user_lookup_mode,
    }
    exec(compile(ast.Module(body=[dispatch], type_ignores=[]), str(FILTER_SOURCE), "exec"), namespace)
    return namespace["process_filter_functions"]


class NativeUsers:
    def __init__(self):
        self.by_id = {"member": types.SimpleNamespace(id="member", email="member@synthetic.invalid")}
        self.by_id["member"].bio = "Synthetic community workshop biography"
        self.by_id["member"].gender = "unspecified"
        self.by_id["member"].date_of_birth = "2000-01-02"
        self.by_id["other"] = types.SimpleNamespace(
            id="other", email="other@example.invalid", bio=None, gender=None, date_of_birth=None
        )
        self.by_email = {
            user.email: user for user in self.by_id.values()
        }
        self.calls = []
        self.delay = 0
        self.failure = None

    def _lookup(self, kind, key, mapping):
        self.calls.append((kind, key, threading.get_ident()))
        if self.delay:
            time.sleep(self.delay)
        if self.failure:
            raise self.failure
        return mapping.get(key)

    def get_user_by_id(self, id):
        return self._lookup("id", id, self.by_id)

    def get_user_by_email(self, email):
        return self._lookup("email", email, self.by_email)

    def other_method(self, value):
        return ("native-other", value)


class FilterFunctionStore:
    def get_function_valves_by_id(self, _filter_id):
        return {}


def make_filter_module(users, *, inlet=None, outlet=None):
    class Filter:
        pass

    module = types.SimpleNamespace(
        id="synthetic-filter",
        Users=users,
        LocalUsers=users,
        Filter=Filter,
    )
    if inlet is not None:
        module.inlet = inlet
    if outlet is not None:
        module.outlet = outlet
    return module


class FilterSdkCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.compat = load_compat()
        self.users = NativeUsers()
        self.process = load_filter_dispatch(self.compat, FilterFunctionStore())

    def request_for(self, module):
        return types.SimpleNamespace(
            app=types.SimpleNamespace(
                state=types.SimpleNamespace(FUNCTIONS={module.id: module})
            )
        )

    def test_exact_filter_bindings_and_alias_are_adapted_without_mutating_native_users(self):
        module = make_filter_module(self.users)
        self.assertTrue(self.compat.adapt_filter_module(module, self.users))
        self.assertIs(module.Users, module.LocalUsers)
        self.assertIs(module.Users._users, self.users)
        self.assertTrue(module.__openatelier_filter_user_lookup_compat__)
        self.assertTrue(getattr(module.Filter(), "__openatelier_filter_user_lookup_compat__"))
        self.assertIsInstance(self.users.get_user_by_id("member"), types.SimpleNamespace)
        self.assertEqual(self.users.other_method("x"), ("native-other", "x"))
        self.assertEqual(module.Users.other_method("x"), ("native-other", "x"))
        error = LookupError("synthetic synchronous lookup error")
        self.users.failure = error
        with self.assertRaises(LookupError) as raised:
            module.Users.get_user_by_id("member")
        self.assertIs(raised.exception, error)
        self.users.failure = None

        non_filter = types.SimpleNamespace(Users=self.users)
        self.assertFalse(self.compat.adapt_filter_module(non_filter, self.users))
        self.assertIs(non_filter.Users, self.users)

    def test_sync_constructor_and_handler_keep_native_result_inside_running_loop(self):
        module = make_filter_module(self.users)

        class Filter:
            def __init__(self):
                self.user = module.Users.get_user_by_id("member")

            def outlet(self, body):
                user = module.LocalUsers.get_user_by_email("member@synthetic.invalid")
                return {**body, "user": user}

        module.Filter = Filter
        self.assertTrue(self.compat.adapt_filter_module(module, self.users))

        async def run():
            self.assertIsNotNone(asyncio.get_running_loop())
            function_module = module.Filter()
            self.assertIs(function_module.user, self.users.by_id["member"])
            # Keep the adapted binding and marker visible on the cached
            # instance used by the actual dispatcher body.
            cached_filter = types.SimpleNamespace(
                id=module.id,
                outlet=function_module.outlet,
                Users=module.Users,
                LocalUsers=module.LocalUsers,
                __openatelier_filter_user_lookup_compat__=True,
            )
            request = self.request_for(cached_filter)
            result, _ = await self.process(
                request=request,
                filter_functions=[cached_filter],
                filter_type="outlet",
                form_data={"value": "synthetic"},
                extra_params={},
            )
            self.assertIs(result["user"], self.users.by_id["member"])
            self.assertEqual([call[0] for call in self.users.calls], ["id", "email"])

        asyncio.run(run())

    def test_async_id_and_email_lookups_run_off_loop_and_allow_heartbeat(self):
        self.users.delay = 0.08
        module = make_filter_module(self.users)

        async def inlet(body):
            by_id = await module.Users.get_user_by_id("member")
            by_email = await module.LocalUsers.get_user_by_email("member@synthetic.invalid")
            return {**body, "users": (by_id, by_email)}

        module.inlet = inlet
        self.assertTrue(self.compat.adapt_filter_module(module, self.users))
        request = self.request_for(module)

        async def run():
            ticks = []

            async def heartbeat():
                await asyncio.sleep(0.01)
                ticks.append("advanced")

            dispatch_result, _ = await asyncio.gather(
                self.process(
                    request=request,
                    filter_functions=[module],
                    filter_type="inlet",
                    form_data={"messages": []},
                    extra_params={},
                ),
                heartbeat(),
            )
            result, _ = dispatch_result
            self.assertEqual(ticks, ["advanced"])
            self.assertIs(result["users"][0], self.users.by_id["member"])
            self.assertIs(result["users"][1], self.users.by_email["member@synthetic.invalid"])
            self.assertEqual(len(self.users.calls), 2)
            self.assertTrue(all(thread_id != threading.get_ident() for _, _, thread_id in self.users.calls))

        asyncio.run(run())

    def test_sync_and_async_handlers_keep_task_local_lookup_modes(self):
        module = make_filter_module(self.users)

        async def inlet(body):
            await asyncio.sleep(0.01)
            value = await module.Users.get_user_by_id("member")
            return {**body, "async_user": value}

        def outlet(body):
            value = module.Users.get_user_by_id("member")
            return {**body, "sync_user": value}

        module.inlet = inlet
        module.outlet = outlet
        self.compat.adapt_filter_module(module, self.users)
        request = self.request_for(module)

        async def run():
            async_dispatch, sync_dispatch = await asyncio.gather(
                self.process(request, [module], "inlet", {}, {}),
                self.process(request, [module], "outlet", {}, {}),
            )
            return async_dispatch, sync_dispatch

        async_dispatch, sync_dispatch = asyncio.run(run())
        result, _ = async_dispatch
        sync_result, _ = sync_dispatch
        self.assertIs(result["async_user"], self.users.by_id["member"])
        self.assertIs(sync_result["sync_user"], self.users.by_id["member"])
        self.assertFalse(inspect.isawaitable(sync_result["sync_user"]))
        self.assertEqual(len(self.users.calls), 2)

    def test_none_errors_and_async_mode_reset_after_error_and_cancellation(self):
        module = make_filter_module(self.users)
        self.compat.adapt_filter_module(module, self.users)

        async def none_inlet(body):
            value = await module.Users.get_user_by_id("missing")
            return {**body, "missing_user": value}

        module.inlet = none_inlet
        request = self.request_for(module)

        async def run_none():
            result, _ = await self.process(request, [module], "inlet", {}, {})
            self.assertIsNone(result["missing_user"])

        asyncio.run(run_none())

        async def error_inlet(body):
            await module.Users.get_user_by_id("missing")
            raise RuntimeError("synthetic filter failure")

        module.inlet = error_inlet

        async def run_error():
            with self.assertRaisesRegex(RuntimeError, "synthetic filter failure"):
                await self.process(request, [module], "inlet", {}, {})
            # Parent task keeps default synchronous behavior after child call.
            self.assertIsNone(module.Users.get_user_by_id("missing"))

        asyncio.run(run_error())

        self.users.failure = LookupError("synthetic native lookup failure")

        async def lookup_error_inlet(body):
            await module.Users.get_user_by_id("member")
            return body

        module.inlet = lookup_error_inlet

        async def run_lookup_error():
            with self.assertRaisesRegex(LookupError, "synthetic native lookup failure"):
                await self.process(request, [module], "inlet", {}, {})
            self.users.failure = None
            self.assertIs(self.users.get_user_by_id("member"), self.users.by_id["member"])

        asyncio.run(run_lookup_error())

        async def waiting_inlet(body):
            await module.Users.get_user_by_email("missing@example.invalid")
            await asyncio.Event().wait()

        module.inlet = waiting_inlet

        async def run_cancel():
            task = asyncio.create_task(self.process(request, [module], "inlet", {}, {}))
            while not self.users.calls:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNone(module.Users.get_user_by_id("missing"))

        asyncio.run(run_cancel())

    def test_pipe_proxy_loop_semantics_remain_unchanged(self):
        module = types.SimpleNamespace(Pipe=object, Users=self.users)
        self.assertTrue(self.compat.adapt_pipe_module(module, self.users))
        self.assertIs(module.Users.get_user_by_id("member"), self.users.by_id["member"])

        async def run():
            result = module.Users.get_user_by_id("member")
            self.assertTrue(inspect.isawaitable(result))
            self.assertIs(await result, self.users.by_id["member"])

        asyncio.run(run())

    def test_measured_async_filter_fixture_runs_through_the_adapted_dispatcher(self):
        fixture_path = ROOT.parents[2] / "experiments/openatelier/clients/filter_lookup_fixtures.py"
        fixture_spec = importlib.util.spec_from_file_location("own_filter_lookup_fixture", fixture_path)
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)

        open_webui = types.ModuleType("open_webui")
        open_webui.__path__ = []
        models = types.ModuleType("open_webui.models")
        models.__path__ = []
        users_module = types.ModuleType("open_webui.models.users")
        users_module.Users = self.users
        fake_modules = {
            "open_webui": open_webui,
            "open_webui.models": models,
            "open_webui.models.users": users_module,
        }
        loaded_filter_module = types.ModuleType("synthetic_loaded_filter")
        with patch.dict(sys.modules, fake_modules):
            exec(fixture.FILTER, loaded_filter_module.__dict__)

        self.assertTrue(self.compat.adapt_filter_module(loaded_filter_module, self.users))
        filter_instance = loaded_filter_module.Filter()
        filter_instance.id = fixture.LOOKUP_ID
        request = self.request_for(filter_instance)

        async def run():
            for user, expected in (
                (
                    {"id": "member", "email": "member@synthetic.invalid", "role": "user"},
                    "Community workshop |member-sdk-ok",
                ),
                (
                    {"id": "other", "email": "other@example.invalid", "role": "user"},
                    "Festival poster |other-sdk-ok",
                ),
            ):
                result, _ = await self.process(
                    request=request,
                    filter_functions=[filter_instance],
                    filter_type="inlet",
                    form_data={"messages": [{"content": expected.split(" |")[0]}]},
                    extra_params={"__user__": user},
                )
                self.assertEqual(result["messages"][-1]["content"], expected)

        asyncio.run(run())

    def test_existing_loader_adapts_filter_before_constructor_only(self):
        tree = ast.parse(PLUGIN_SOURCE.read_text(encoding="utf-8"), filename=str(PLUGIN_SOURCE))
        loader = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "load_function_module_by_id"
        )
        filter_branch = next(
            node for node in ast.walk(loader)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Name)
            and node.test.func.id == "hasattr"
            and len(node.test.args) == 2
            and isinstance(node.test.args[1], ast.Constant)
            and node.test.args[1].value == "Filter"
        )
        calls = [
            node for node in ast.walk(filter_branch)
            if isinstance(node, ast.Call)
        ]
        adapt_index = next(
            index for index, call in enumerate(calls)
            if isinstance(call.func, ast.Name) and call.func.id == "adapt_filter_module"
        )
        constructor_index = next(
            index for index, call in enumerate(calls)
            if isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "module"
            and call.func.attr == "Filter"
        )
        self.assertLess(adapt_index, constructor_index)
        self.assertNotIn("install_frontmatter_requirements", [
            call.func.id for call in calls if isinstance(call.func, ast.Name)
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
