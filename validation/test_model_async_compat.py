# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Isolated tests for explicit model bootstrap and plugin lookup adapters."""

import asyncio
import ast
import builtins
import hashlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPAT_PATH = ROOT / "backend/open_webui/utils/plugin_user_compat.py"
PLUGIN_PATH = ROOT / "backend/open_webui/utils/plugin.py"
BOOTSTRAP_PATH = ROOT / "backend/open_webui/utils/async_model_bootstrap.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingStore:
    def __init__(self, methods):
        self.methods = frozenset(methods)
        self.calls = []
        self.fail = None
        self.delay = 0
        self.return_none = False

    def __getattr__(self, name):
        if name not in self.methods:
            raise AttributeError(name)

        def invoke(*args, **kwargs):
            self.calls.append((name, args, kwargs, threading.get_ident()))
            if self.delay:
                time.sleep(self.delay)
            if self.fail is not None:
                raise self.fail
            if self.return_none:
                return None
            return (name, args, kwargs)

        return invoke


class FilterImportCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.compat = load_module("test_plugin_user_compat", COMPAT_PATH)
        self.users = RecordingStore(("get_user_by_id", "get_user_by_email"))
        self.files = RecordingStore(("get_file_by_id",))
        self.chats = RecordingStore(("get_chat_by_id_and_user_id",))
        self.old_modules = {name: sys.modules.get(name) for name in (
            "open_webui", "open_webui.models", "open_webui.models.files",
            "open_webui.models.chats", "open_webui.models.users")}
        root = types.ModuleType("open_webui")
        models = types.ModuleType("open_webui.models")
        files_module = types.ModuleType("open_webui.models.files")
        chats_module = types.ModuleType("open_webui.models.chats")
        users_module = types.ModuleType("open_webui.models.users")
        files_module.Files = self.files
        chats_module.Chats = self.chats
        users_module.Users = self.users
        root.models = models
        models.files = files_module
        models.chats = chats_module
        sys.modules.update({
            "open_webui": root,
            "open_webui.models": models,
            "open_webui.models.files": files_module,
            "open_webui.models.chats": chats_module,
            "open_webui.models.users": users_module,
        })
        self.module = types.ModuleType("synthetic_filter")
        self.module.__builtins__ = builtins
        self.module.Users = self.users
        self.module.LocalFiles = self.files
        self.module.Files = self.files
        self.module.Chats = self.chats
        self.module.importlib = __import__("importlib")
        self.module.import_module = importlib.import_module
        self.module.FileModule = sys.modules["open_webui.models.files"]
        self.module.ChatsModule = sys.modules["open_webui.models.chats"]
        self.module.Filter = type("Filter", (), {})
        self.original_builtins = self.module.__builtins__
        self.assertTrue(self.compat.adapt_filter_module(
            self.module, self.users, self.files, self.chats, adapt_models=True))

    def tearDown(self):
        for name, module in self.old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_all_selected_methods_keep_arguments_results_none_errors_and_run_off_loop(self):
        async def run():
            calls = (
                (self.module.Users.get_user_by_id, ("user-id",), {}),
                (self.module.Users.get_user_by_email, ("user@example.invalid",), {}),
                (self.module.Files.get_file_by_id, ("file-id",), {}),
                (self.module.Chats.get_chat_by_id_and_user_id, ("chat-id", "owner-id"), {}),
            )
            results = []
            for method, args, kwargs in calls:
                result = method(*args, **kwargs)
                if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            self.assertEqual(results[0], ("get_user_by_id", ("user-id",), {}))
            self.assertEqual(results[1], ("get_user_by_email", ("user@example.invalid",), {}))
            self.assertEqual(results[2], ("get_file_by_id", ("file-id",), {}))
            self.assertEqual(results[3], ("get_chat_by_id_and_user_id", ("chat-id", "owner-id"), {}))
            self.assertTrue(all(call[3] != threading.get_ident() for store in
                                (self.users, self.files, self.chats) for call in store.calls))
        token = self.compat.set_filter_async_user_lookup_mode(True)
        try:
            asyncio.run(run())
        finally:
            self.compat.reset_filter_async_user_lookup_mode(token)

        missing = RecordingStore(("get_file_by_id",))
        missing.get_file_by_id = lambda _id: None
        self.assertIsNone(missing.get_file_by_id("missing"))
        error = LookupError("synthetic lookup failure")
        self.files.fail = error

        async def fail():
            token = self.compat.set_filter_async_user_lookup_mode(True)
            try:
                await self.module.Files.get_file_by_id("file-id")
            finally:
                self.compat.reset_filter_async_user_lookup_mode(token)

        with self.assertRaises(LookupError) as raised:
            asyncio.run(fail())
        self.assertIs(raised.exception, error)

    def test_direct_alias_dynamic_imports_and_non_target_imports_are_local(self):
        self.assertIs(self.module.Files, self.module.LocalFiles)
        self.assertIs(self.module.Files._target, self.files)
        self.assertIs(self.module.FileModule.Files._target, self.files)
        self.assertIs(self.module.ChatsModule.Chats._target, self.chats)
        dynamic = self.module.importlib.import_module("open_webui.models.chats")
        self.assertIs(dynamic.Chats._target, self.chats)
        self.assertIs(self.module.import_module("open_webui.models.chats").Chats._target, self.chats)
        self.assertIs(sys.modules["open_webui.models.files"], self.module.FileModule._wrapped)
        self.assertIs(self.module.__builtins__, self.original_builtins)
        self.assertEqual(self.files.get_file_by_id("native")[0], "get_file_by_id")
        import math
        self.assertIs(self.module.importlib.import_module("math"), math)
        self.assertIs(self.module.importlib.__dict__["_wrapped"], importlib)
        self.assertFalse(self.compat.adapt_filter_module(types.SimpleNamespace(), self.users))

    def test_repeated_adaptation_is_idempotent(self):
        first = self.module.Files
        self.assertTrue(self.compat.adapt_filter_module(
            self.module, self.users, self.files, self.chats, adapt_models=True))
        self.assertIs(self.module.Files, first)

        async def run():
            token = self.compat.set_filter_async_user_lookup_mode(True)
            try:
                self.assertEqual(
                    await self.module.Files.get_file_by_id("once"),
                    ("get_file_by_id", ("once",), {}),
                )
            finally:
                self.compat.reset_filter_async_user_lookup_mode(token)

        asyncio.run(run())
        self.assertEqual(sum(call[0] == "get_file_by_id" for call in self.files.calls), 1)

    def test_sync_filter_context_remains_native_and_aliases_preserve_other_methods(self):
        token = self.compat.set_filter_async_user_lookup_mode(False)
        try:
            result = self.module.Files.get_file_by_id("sync")
        finally:
            self.compat.reset_filter_async_user_lookup_mode(token)
        self.assertEqual(result, ("get_file_by_id", ("sync",), {}))
        self.assertEqual(self.files.calls[-1][3], threading.get_ident())
        self.assertTrue(self.module.__openatelier_filter_user_lookup_compat__)

        self.files.delay = 0.06
        async def responsive():
            token = self.compat.set_filter_async_user_lookup_mode(True)
            try:
                ticks = []

                async def lookup():
                    return await self.module.Files.get_file_by_id("slow")

                async def heartbeat():
                    await asyncio.sleep(0.01)
                    ticks.append(True)

                result, _ = await asyncio.gather(lookup(), heartbeat())
                self.assertTrue(ticks)
                self.assertEqual(result[1], ("slow",))
            finally:
                self.compat.reset_filter_async_user_lookup_mode(token)

        asyncio.run(responsive())
        self.files.return_none = True

        async def none_result():
            token = self.compat.set_filter_async_user_lookup_mode(True)
            try:
                self.assertIsNone(await self.module.Files.get_file_by_id("missing"))
            finally:
                self.compat.reset_filter_async_user_lookup_mode(token)

        asyncio.run(none_result())


class FilterModelAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.compat = load_module("test_plugin_user_compat_allowlist", COMPAT_PATH)
        self.functions = RecordingStore(("update_function_by_id",))
        self.files = RecordingStore(("get_file_by_id",))
        self.old_modules = {}
        root = types.ModuleType("open_webui")
        root.__path__ = []
        utils = types.ModuleType("open_webui.utils")
        utils.__path__ = []
        models = types.ModuleType("open_webui.models")
        models.__path__ = []
        files_module = types.ModuleType("open_webui.models.files")
        files_module.Files = self.files
        for name, value in {
            "open_webui": root,
            "open_webui.utils": utils,
            "open_webui.utils.plugin_user_compat": self.compat,
            "open_webui.models": models,
            "open_webui.models.files": files_module,
        }.items():
            self.old_modules[name] = sys.modules.get(name)
            sys.modules[name] = value
        root.utils = utils
        root.models = models
        models.files = files_module
        tree = ast.parse(PLUGIN_PATH.read_text(encoding="utf-8"))
        function = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "load_function_module_by_id")
        namespace = {
            "os": os, "sys": sys, "types": types, "tempfile": tempfile,
            "Functions": self.functions, "replace_imports": lambda content: content,
            "extract_frontmatter": lambda _content: {},
            "install_frontmatter_requirements": lambda _requirements: None,
            "log": types.SimpleNamespace(info=lambda *args, **kwargs: None,
                                          error=lambda *args, **kwargs: None),
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]),
                     str(PLUGIN_PATH), "exec"), namespace)
        self.load = namespace["load_function_module_by_id"]

    def tearDown(self):
        for name, value in self.old_modules.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def _load_filter(self, function_id):
        source = (
            "from open_webui.models.files import Files\\n"
            "class Filter:\\n"
            "    async def lookup(self):\\n"
            "        return await Files.get_file_by_id('missing')\\n"
        ).replace("\\n", "\n")
        return self.load(function_id, source)[0]

    def test_default_and_unselected_filters_keep_native_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENATELIER_ASYNC_FILTER_MODEL_IDS", None)
            instance = self._load_filter("legacy-filter")
        module = sys.modules["function_legacy-filter"]
        self.assertIs(module.Files, self.files)
        self.assertFalse(getattr(module, "__openatelier_filter_model_lookup_compat__", False))
        with self.assertRaises(TypeError):
            asyncio.run(instance.lookup())

    def test_only_exact_selected_loader_id_gets_awaitable_lookup(self):
        with patch.dict(os.environ, {"OPENATELIER_ASYNC_FILTER_MODEL_IDS": '["selected-filter"]'}):
            selected = self._load_filter("selected-filter")
            other = self._load_filter("other-filter")
        selected_module = sys.modules["function_selected-filter"]
        other_module = sys.modules["function_other-filter"]
        self.assertTrue(selected_module.__openatelier_filter_model_lookup_compat__)
        self.assertIs(other_module.Files, self.files)
        self.files.return_none = True
        self.assertIsNone(other_module.Files.get_file_by_id("native-none"))

        async def invoke(instance):
            token = self.compat.set_filter_async_user_lookup_mode(True)
            try:
                return await instance.lookup()
            finally:
                self.compat.reset_filter_async_user_lookup_mode(token)

        self.assertIsNone(asyncio.run(invoke(selected)))
        with self.assertRaises(TypeError):
            asyncio.run(invoke(other))

    def test_allowlist_bounds_invalid_values_without_disclosing_them(self):
        invalid = (
            "not-json", "{}", '"one-id"', '["a", "a"]', '["*"]',
            '["has space"]', '["é"]', '["x/y"]',
            "[\"" + "x" * 129 + "\"]",
            "[\"a\"]" * 800,
            "[" + ",".join('"id-%s"' % index for index in range(33)) + "]",
        )
        for raw in invalid:
            with self.subTest(raw=raw[:24]):
                with patch.dict(os.environ, {"OPENATELIER_ASYNC_FILTER_MODEL_IDS": raw}):
                    with self.assertRaises(self.compat.FilterModelCompatConfigError) as raised:
                        self.compat.read_async_filter_model_ids()
                self.assertEqual(str(raised.exception), "Invalid async Filter model allowlist")
        with patch.dict(os.environ, {"OPENATELIER_ASYNC_FILTER_MODEL_IDS": '["a","B-2","x.y_3"]'}):
            self.assertEqual(self.compat.read_async_filter_model_ids(), frozenset(("a", "B-2", "x.y_3")))
        with patch.dict(os.environ, {"OPENATELIER_ASYNC_FILTER_MODEL_IDS": ""}):
            self.assertEqual(self.compat.read_async_filter_model_ids(), frozenset())

    def test_invalid_loader_configuration_does_not_deactivate_function(self):
        with patch.dict(os.environ, {"OPENATELIER_ASYNC_FILTER_MODEL_IDS": '["x", "x"]'}):
            with self.assertRaises(self.compat.FilterModelCompatConfigError):
                self._load_filter("legacy-filter")
        self.assertEqual(self.functions.calls, [])


class AsyncModelBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = load_module("test_async_model_bootstrap", BOOTSTRAP_PATH)

    def test_six_model_methods_preserve_binding_and_scope_restores_after_success_or_error(self):
        function_names = ("get_function_by_id", "insert_new_function", "update_function_by_id")
        model_names = ("get_model_by_id", "insert_new_model", "update_model_by_id")
        functions = types.SimpleNamespace(Functions=RecordingStore(function_names))
        models = types.SimpleNamespace(Models=RecordingStore(model_names))
        original_functions, original_models = functions.Functions, models.Models

        async def invoke_all():
            for method, args, expected in (
                (functions.Functions.get_function_by_id, ("f-id",), ("get_function_by_id", ("f-id",), {})),
                (functions.Functions.insert_new_function, ("user-id", "filter", {"x": 1}),
                 ("insert_new_function", ("user-id", "filter", {"x": 1}), {})),
                (functions.Functions.update_function_by_id, ("f-id", {"name": "new"}),
                 ("update_function_by_id", ("f-id", {"name": "new"}), {})),
                (models.Models.get_model_by_id, ("m-id",), ("get_model_by_id", ("m-id",), {})),
                (models.Models.insert_new_model, ({"name": "m"}, "user-id"),
                 ("insert_new_model", ({"name": "m"}, "user-id"), {})),
                (models.Models.update_model_by_id, ("m-id", {"base_model_id": "b"}),
                 ("update_model_by_id", ("m-id", {"base_model_id": "b"}), {})),
            ):
                self.assertEqual(await method(*args), expected)

        with self.bootstrap._model_api_scope(functions, models):
            self.assertIsNot(functions.Functions, original_functions)
            self.assertIsNot(models.Models, original_models)
            asyncio.run(invoke_all())
            original_models.return_none = True
            async def check_none():
                self.assertIsNone(await models.Models.get_model_by_id("missing"))
            asyncio.run(check_none())
            original_models.return_none = False
            original_functions.fail = LookupError("synthetic model lookup failure")
            async def check_error():
                with self.assertRaises(LookupError) as raised:
                    await functions.Functions.get_function_by_id("error")
                self.assertIs(raised.exception, original_functions.fail)
            asyncio.run(check_error())
            original_functions.fail = None
            original_models.delay = 0.06
            async def check_heartbeat():
                ticks = []
                async def heartbeat():
                    await asyncio.sleep(0.01)
                    ticks.append(True)
                await asyncio.gather(models.Models.get_model_by_id("slow"), heartbeat())
                self.assertTrue(ticks)
            asyncio.run(check_heartbeat())
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                with self.bootstrap._model_api_scope(functions, models):
                    pass
        self.assertIs(functions.Functions, original_functions)
        self.assertIs(models.Models, original_models)
        self.assertEqual([call[:3] for call in original_functions.calls[:3]], [
            ("get_function_by_id", ("f-id",), {}),
            ("insert_new_function", ("user-id", "filter", {"x": 1}), {}),
            ("update_function_by_id", ("f-id", {"name": "new"}), {}),
        ])
        self.assertEqual([call[:3] for call in original_models.calls[:3]], [
            ("get_model_by_id", ("m-id",), {}),
            ("insert_new_model", ({"name": "m"}, "user-id"), {}),
            ("update_model_by_id", ("m-id", {"base_model_id": "b"}), {}),
        ])
        self.assertTrue(all(call[3] != threading.get_ident() for call in
                            original_functions.calls + original_models.calls))

        with self.assertRaisesRegex(RuntimeError, "scope failure"):
            with self.bootstrap._model_api_scope(functions, models):
                raise RuntimeError("scope failure")
        self.assertIs(functions.Functions, original_functions)
        self.assertIs(models.Models, original_models)

    def test_verified_script_refuses_invalid_files_and_launch_uses_verified_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bootstrap.py"
            original = b"raise SystemExit(0)\n"
            path.write_bytes(original)
            digest = hashlib.sha256(original).hexdigest()
            self.assertEqual(self.bootstrap._read_verified_script(path, digest), original)
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                self.bootstrap._read_verified_script(path, "0" * 64)
            link = pathlib.Path(directory) / "link.py"
            link.symlink_to(path)
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                self.bootstrap._read_verified_script(link, digest)
            path.write_bytes(b"x" * (self.bootstrap.MAX_BOOTSTRAP_BYTES + 1))
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                self.bootstrap._read_verified_script(path, hashlib.sha256(path.read_bytes()).hexdigest())
            path.write_bytes(original)
            fifo = pathlib.Path(directory) / "bootstrap.fifo"
            os.mkfifo(fifo)
            started = time.monotonic()
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                self.bootstrap._read_verified_script(fifo, digest)
            self.assertLess(time.monotonic() - started, 1.0)
            captured = {}

            def fake_run(argv, **kwargs):
                captured.update(argv=argv, **kwargs)
                path.write_bytes(b"changed after validation")
                return types.SimpleNamespace(returncode=0)

            with patch.object(self.bootstrap.subprocess, "run", side_effect=fake_run):
                result = self.bootstrap.run_verified_bootstrap(path, digest, ["--synthetic"])
            self.assertEqual(result, 0)
            self.assertEqual(captured["input"], original)
            self.assertNotIn("shell", captured)
            self.assertEqual(captured["stdout"], self.bootstrap.subprocess.DEVNULL)
            with self.assertRaises(self.bootstrap.BootstrapRefused):
                self.bootstrap.run_verified_bootstrap(path, digest, ["bad\x00arg"])

    def test_module_cli_uses_explicit_arguments_and_fixed_failures(self):
        calls = []

        def run(path, digest, args):
            calls.append((path, digest, args))
            return 7

        with patch.object(self.bootstrap, "run_verified_bootstrap", side_effect=run):
            self.assertEqual(self.bootstrap.main([]), 2)
            self.assertEqual(self.bootstrap.main(["script.py", "a" * 64, "--mode", "one"]), 7)
        self.assertEqual(calls, [("script.py", "a" * 64, ["--mode", "one"])])
        with patch.object(self.bootstrap, "run_verified_bootstrap",
                          side_effect=self.bootstrap.BootstrapRefused("private detail")):
            self.assertEqual(self.bootstrap.main(["script.py", "x"]), 2)
        with patch.object(self.bootstrap, "run_verified_bootstrap",
                          side_effect=self.bootstrap.BootstrapUnavailable("private detail")):
            self.assertEqual(self.bootstrap.main(["script.py", "x"]), 1)

    def test_child_imports_config_before_running_verified_script_and_restores_bindings(self):
        events = []
        functions = types.ModuleType("open_webui.models.functions")
        models = types.ModuleType("open_webui.models.models")
        native_functions = RecordingStore(("get_function_by_id",))
        native_models = RecordingStore(("get_model_by_id",))
        functions.Functions = native_functions
        models.Models = native_models
        config = types.ModuleType("open_webui.config")
        packages = {
            "open_webui": types.ModuleType("open_webui"),
            "open_webui.models": types.ModuleType("open_webui.models"),
            "open_webui.models.functions": functions,
            "open_webui.models.models": models,
            "open_webui.config": config,
        }
        packages["open_webui"].models = packages["open_webui.models"]
        packages["open_webui.models"].functions = functions
        packages["open_webui.models"].models = models
        previous = {name: sys.modules.get(name) for name in packages}
        sys.modules.update(packages)
        original_import = self.bootstrap.importlib.import_module
        original_run = asyncio.run

        def imported(name, *args, **kwargs):
            if name == "open_webui.config":
                events.append("config")
            elif name.startswith("open_webui.models."):
                events.append("models")
            if name in packages:
                return packages[name]
            return original_import(name, *args, **kwargs)

        def tracked_run(coro, *args, **kwargs):
            events.append("external-loop")
            return original_run(coro, *args, **kwargs)

        script = (
            "import asyncio\n"
            "from open_webui.models.functions import Functions\n"
            "async def use_models():\n"
            "    assert await Functions.get_function_by_id('selected') == "
            "('get_function_by_id', ('selected',), {})\n"
            "asyncio.run(use_models())\n"
        ).encode()
        old_stdin, old_argv = sys.stdin, sys.argv
        sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(script))
        sys.argv = ["child", "synthetic.py", hashlib.sha256(script).hexdigest()]
        try:
            with patch.object(self.bootstrap.importlib, "import_module", side_effect=imported), \
                 patch.object(asyncio, "run", side_effect=tracked_run):
                self.assertEqual(self.bootstrap._child_main(), 0)
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
        self.assertLess(events.index("config"), events.index("external-loop"))
        self.assertIs(functions.Functions, native_functions)
        self.assertIs(models.Models, native_models)

        failed_script = b"raise RuntimeError('synthetic failure')\n"
        sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(failed_script))
        sys.argv = ["child", "synthetic.py", hashlib.sha256(failed_script).hexdigest()]
        try:
            with patch.object(self.bootstrap.importlib, "import_module", side_effect=imported):
                self.assertEqual(self.bootstrap._child_main(), 1)
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv
        self.assertIs(functions.Functions, native_functions)
        self.assertIs(models.Models, native_models)

        no_code_script = b"raise SystemExit()\n"
        sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(no_code_script))
        sys.argv = ["child", "synthetic.py", hashlib.sha256(no_code_script).hexdigest()]
        try:
            with patch.object(self.bootstrap.importlib, "import_module", side_effect=imported):
                self.assertEqual(self.bootstrap._child_main(), 0)
        finally:
            sys.stdin, sys.argv = old_stdin, old_argv


if __name__ == "__main__":
    unittest.main(verbosity=2)
