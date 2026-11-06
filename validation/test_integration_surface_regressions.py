# SPDX-License-Identifier: BSD-3-Clause
"""Behavioral regressions for multimodal titles and disabled release-check egress."""
import ast
import asyncio
import copy
import json
import logging
import re
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError("Missing tested source function")
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), path, "exec"), namespace)
    return namespace


class IntegrationSurfaceRegressions(unittest.TestCase):
    def test_multimodal_title_input_keeps_text_and_preserves_original(self):
        ns = functions("backend/open_webui/routers/tasks.py", {"title_message_text"}, {"re": re})
        content = [{"type": "text", "text": "Festival poster"}, {"type": "image_url", "image_url": {"url": "data:synthetic"}}, {"type": "text", "text": "Use blue"}]
        original = copy.deepcopy(content)
        self.assertEqual(ns["title_message_text"](content), "Festival poster\nUse blue")
        self.assertEqual(content, original)
        self.assertEqual(ns["title_message_text"](None), "")

    def test_title_accepts_json_fenced_json_and_plain_provider_result(self):
        parse = functions("backend/open_webui/utils/middleware.py", {"_generated_chat_title"}, {"json": json})["_generated_chat_title"]
        for text in ['{"title":"Community workshop"}', '```json\n{"title":"Community workshop"}\n```', 'Community workshop']:
            self.assertEqual(parse({"choices": [{"message": {"content": text}}]}), "Community workshop")
        for value in [None, {"detail": "error"}, {"choices": []}, {"choices": [{"message": {"content": []}}]}, {"choices": [{"message": {"content": '{"title":[]}'}}]}]:
            self.assertEqual(parse(value), "")

    def test_disabled_or_offline_release_check_never_creates_network_session(self):
        class NoNetwork:
            def __getattr__(self, _name):
                raise AssertionError("Unexpected outbound release check")
        for offline, enabled in [(False, None), (False, "false"), (True, "true")]:
            environment = {} if enabled is None else {"OPENATELIERUI_ENABLE_RELEASE_CHECK": enabled}
            ns = functions("backend/open_webui/main.py", {"get_app_latest_release_version"}, {
                "os": types.SimpleNamespace(environ=environment), "OFFLINE_MODE": offline,
                "VERSION": "synthetic", "aiohttp": NoNetwork(), "log": logging.getLogger(__name__),
                "Depends": lambda _value: None, "get_verified_user": None,
            })
            result = asyncio.run(ns["get_app_latest_release_version"](user=object()))
            self.assertEqual(result["current"], "synthetic")
            self.assertFalse(result["check_enabled"])


if __name__ == "__main__":
    unittest.main()
