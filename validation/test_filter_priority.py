# SPDX-License-Identifier: BSD-3-Clause
"""Source-bound tests for selected filter priority resolution."""

import ast
import inspect
import pathlib
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FILTER_SOURCE = ROOT / "backend/open_webui/utils/filter.py"
MIDDLEWARE_SOURCE = ROOT / "backend/open_webui/utils/middleware.py"


class Field:
    def __init__(self, default=None, required=False):
        self.default = default
        self.required = required

    def is_required(self):
        return self.required


class FunctionRow:
    def __init__(self, function_id):
        self.id = function_id


class FunctionStore:
    def __init__(self, active_ids, valves=None):
        self.active = [FunctionRow(function_id) for function_id in active_ids]
        self.valves = valves or {}
        self.calls = []

    def get_global_filter_functions(self):
        self.calls.append(("global",))
        return []

    def get_functions_by_type(self, function_type, active_only=False):
        self.calls.append(("active", function_type, active_only))
        return self.active

    def get_function_valves_by_id(self, function_id):
        self.calls.append(("valves", function_id))
        return self.valves.get(function_id, {})


def module_with_default(priority=None, *, missing=False):
    if missing:
        fields = {}
    else:
        fields = {"priority": Field(priority)}
    valves = type("Valves", (), {"model_fields": fields})
    return types.SimpleNamespace(Valves=valves)


def load_subject(store, modules, loads, failures=None):
    tree = ast.parse(FILTER_SOURCE.read_text(encoding="utf-8"), filename=str(FILTER_SOURCE))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_sorted_filter_ids"
    )

    def loader(function_id):
        loads.append(function_id)
        if failures and function_id in failures:
            raise failures[function_id]
        return modules[function_id], "filter", {}

    namespace = {
        "inspect": inspect,
        "Functions": store,
        "load_function_module_by_id": loader,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(FILTER_SOURCE), "exec"), namespace)
    return namespace["get_sorted_filter_ids"]


class FilterPriorityTests(unittest.TestCase):
    def test_reversed_selection_uses_declared_defaults_and_request_cache(self):
        modules = {
            "sync": module_with_default(0),
            "async": module_with_default(10),
            "inactive": module_with_default(-100),
        }
        store = FunctionStore(["sync", "async"])
        loads = []
        get_sorted = load_subject(store, modules, loads)
        cache = {}
        request = types.SimpleNamespace(
            app=types.SimpleNamespace(state=types.SimpleNamespace(FUNCTIONS=cache))
        )
        model = {"info": {"meta": {"filterIds": ["async", "sync", "inactive"]}}}

        self.assertEqual(get_sorted(model, request=request), ["sync", "async"])
        self.assertEqual(set(loads), {"async", "sync"})
        self.assertEqual(set(cache), {"async", "sync"})
        self.assertEqual(get_sorted(model, request=request), ["sync", "async"])
        self.assertEqual(len(loads), 2)
        self.assertEqual(set(loads), {"async", "sync"})

    def test_saved_priority_overrides_default_and_partial_values_use_default(self):
        modules = {"sync": module_with_default(0), "async": module_with_default(10)}
        store = FunctionStore(["sync", "async"], {"sync": {"priority": 20}, "async": {"other": True}})
        loads = []
        get_sorted = load_subject(store, modules, loads)
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(FUNCTIONS={})))
        model = {"info": {"meta": {"filterIds": ["sync", "async"]}}}
        self.assertEqual(get_sorted(model, request=request), ["async", "sync"])
        self.assertEqual(set(loads), {"async"})
        store.valves["sync"] = {}
        self.assertEqual(get_sorted(model, request=request), ["sync", "async"])
        self.assertEqual(len(loads), 2)
        self.assertEqual(set(loads), {"async", "sync"})

    def test_context_free_explicit_overrides_do_not_load_selected_modules(self):
        modules = {"sync": module_with_default(0), "async": module_with_default(10)}
        store = FunctionStore(
            ["sync", "async"],
            {"sync": {"priority": 20}, "async": {"priority": 10}},
        )
        loads = []
        get_sorted = load_subject(store, modules, loads)
        model = {"info": {"meta": {"filterIds": ["sync", "async"]}}}
        self.assertEqual(get_sorted(model), ["async", "sync"])
        self.assertEqual(loads, [])

    def test_defaultless_valves_falls_back_to_zero_and_positional_api_remains_valid(self):
        modules = {"plain": module_with_default(missing=True)}
        store = FunctionStore(["plain"])
        loads = []
        get_sorted = load_subject(store, modules, loads)
        self.assertEqual(get_sorted({"info": {"meta": {"filterIds": ["plain"]}}}), ["plain"])
        self.assertEqual(loads, ["plain"])

    def test_plain_declared_priority_default_is_respected(self):
        class Valves:
            priority = -3

        modules = {
            "plain": types.SimpleNamespace(Valves=Valves),
            "zero": module_with_default(0),
        }
        store = FunctionStore(["plain", "zero"])
        loads = []
        get_sorted = load_subject(store, modules, loads)
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(FUNCTIONS={})))
        self.assertEqual(
            get_sorted({"info": {"meta": {"filterIds": ["zero", "plain"]}}}, request=request),
            ["plain", "zero"],
        )

    def test_selected_loader_failure_propagates(self):
        error = RuntimeError("synthetic selected filter load failure")
        store = FunctionStore(["selected"])
        loads = []
        get_sorted = load_subject(store, {}, loads, {"selected": error})
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(FUNCTIONS={})))
        with self.assertRaisesRegex(RuntimeError, "synthetic selected filter"):
            get_sorted({"info": {"meta": {"filterIds": ["selected"]}}}, request=request)
        self.assertEqual(loads, ["selected"])
        self.assertEqual(request.app.state.FUNCTIONS, {})

    def test_middleware_passes_existing_request_at_the_two_sorting_sites(self):
        tree = ast.parse(MIDDLEWARE_SOURCE.read_text(encoding="utf-8"), filename=str(MIDDLEWARE_SOURCE))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_sorted_filter_ids"
        ]
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            all(
                any(keyword.arg == "request" and isinstance(keyword.value, ast.Name) and keyword.value.id == "request" for keyword in call.keywords)
                for call in calls
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
