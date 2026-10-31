# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-isolated controls for the opt-in local encoder startup wrapper."""

import ast
import importlib.util
import os
import pathlib
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "backend/open_webui/utils/lazy_local_embedding.py"
MAIN_PATH = ROOT / "backend/open_webui/main.py"
SPEC = importlib.util.spec_from_file_location("lazy_local_embedding", HELPER_PATH)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def _startup_block():
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        assigned = {
            target.attr
            for statement in ast.walk(node)
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Attribute)
        }
        if {"ef", "rf"}.issubset(assigned):
            return node
    raise AssertionError("startup encoder/factory block not found")


class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, *args, **kwargs):
        result = object()
        self.calls.append((args, kwargs, result))
        return result


class LazyLocalEmbeddingTests(unittest.TestCase):
    def test_flag_is_strictly_default_off(self):
        name = helper.LAZY_LOCAL_EMBEDDING_STARTUP_ENV
        for value in (None, "", "0", "true", "yes"):
            with self.subTest(value=value), patch.dict(os.environ, {}, clear=True):
                if value is not None:
                    os.environ[name] = value
                self.assertFalse(helper.lazy_local_embedding_startup_enabled())
        with patch.dict(os.environ, {name: "1"}, clear=True):
            self.assertTrue(helper.lazy_local_embedding_startup_enabled())

    def test_captured_parameters_and_encode_forwarding(self):
        calls = []
        encoder = FakeEncoder()

        def factory(*args):
            calls.append(args)
            return encoder

        deferred = helper.DeferredLocalEmbedding(factory, "", "synthetic/model", True)
        self.assertEqual(calls, [])
        payload = ["alpha", "beta"]
        result = deferred.encode(payload, prompt="query", normalize_embeddings=False)
        self.assertIs(result, encoder.calls[0][2])
        self.assertEqual(calls, [("", "synthetic/model", True)])
        self.assertEqual(
            encoder.calls,
            [((payload,), {"prompt": "query", "normalize_embeddings": False}, result)],
        )
        self.assertIs(deferred.encode("warm"), encoder.calls[1][2])
        self.assertEqual(len(calls), 1)

    def test_concurrent_first_encode_is_single_flight(self):
        calls = []
        encoder = FakeEncoder()

        def factory(engine, model, auto_update):
            calls.append((engine, model, auto_update))
            time.sleep(0.03)
            return encoder

        deferred = helper.DeferredLocalEmbedding(factory, "", "local/test", False)
        start = threading.Barrier(9)
        results = []
        errors = []

        def worker(index):
            try:
                start.wait(timeout=2)
                results.append(deferred.encode(index))
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        start.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 8)
        self.assertEqual(calls, [("", "local/test", False)])

    def test_none_result_is_visible_and_is_not_retried(self):
        calls = []
        deferred = helper.DeferredLocalEmbedding(
            lambda *args: calls.append(args), "", "local/missing", False
        )
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "returned no encoder"):
                deferred.encode("sample")
        self.assertEqual(calls, [("", "local/missing", False)])

    def test_factory_error_is_visible_and_is_not_retried(self):
        calls = []
        failure = OSError("synthetic local model unavailable")

        def factory(*args):
            calls.append(args)
            raise failure

        deferred = helper.DeferredLocalEmbedding(factory, "", "local/error", True)
        for _ in range(2):
            with self.assertRaises(OSError) as raised:
                deferred.encode("sample")
            self.assertIs(raised.exception, failure)
        self.assertEqual(calls, [("", "local/error", True)])

    def test_main_startup_selects_only_opted_in_nonempty_local_model(self):
        startup = _startup_block()
        local_cases = (
            ("", "local/model", True),
            ("openai", "local/model", False),
            ("", "", False),
        )
        for engine, model, enabled in local_cases:
            with self.subTest(engine=engine, model=model):
                self._run_startup_block(engine, model, "1", enabled)

    def test_default_off_path_remains_eager(self):
        self._run_startup_block("", "local/model", None, False)

    def _run_startup_block(self, engine, model, flag, expect_deferred):
        startup = _startup_block()
        encoder = FakeEncoder()
        factory_calls = []
        reranker_calls = []

        def get_ef(*args):
            factory_calls.append(args)
            return encoder

        def get_rf(*args):
            reranker_calls.append(args)
            return "reranker"

        config = types.SimpleNamespace(
            RAG_EMBEDDING_ENGINE=engine,
            RAG_EMBEDDING_MODEL=model,
            RAG_RERANKING_MODEL="synthetic/reranker",
        )
        app = types.SimpleNamespace(state=types.SimpleNamespace(config=config))
        namespace = {
            "app": app,
            "get_ef": get_ef,
            "get_rf": get_rf,
            "RAG_EMBEDDING_MODEL_AUTO_UPDATE": True,
            "RAG_RERANKING_MODEL_AUTO_UPDATE": False,
            "DeferredLocalEmbedding": helper.DeferredLocalEmbedding,
            "lazy_local_embedding_startup_enabled": lambda: flag == "1",
        }
        exec(compile(ast.Module(body=[startup], type_ignores=[]), str(MAIN_PATH), "exec"), namespace)
        self.assertEqual(reranker_calls, [("synthetic/reranker", False)])
        if expect_deferred:
            self.assertIsInstance(app.state.ef, helper.DeferredLocalEmbedding)
            self.assertEqual(factory_calls, [])
            self.assertIs(app.state.ef.encode("first"), encoder.calls[0][2])
            self.assertEqual(factory_calls, [(engine, model, True)])
        else:
            self.assertIs(app.state.ef, encoder)
            self.assertEqual(factory_calls, [(engine, model, True)])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LazyLocalEmbeddingTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
