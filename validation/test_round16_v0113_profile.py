#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Whole-family controls for the versioned Open WebUI v0.11.3 profile."""

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"
RESPONSE = ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
USER_MESSAGE = ROOT / "src/lib/components/chat/Messages/UserMessage.svelte"
SIDEBAR = ROOT / "src/lib/components/layout/Sidebar.svelte"
CHAT_ITEM = ROOT / "src/lib/components/layout/Sidebar/ChatItem.svelte"
FOLDER = ROOT / "src/lib/components/common/Folder.svelte"
NAVBAR = ROOT / "src/lib/components/chat/Navbar.svelte"
APP_CSS = ROOT / "src/app.css"
STORES = ROOT / "src/lib/stores/index.ts"


def extracted_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in selected} != set(names):
        raise AssertionError("expected source functions were not found")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class Round16V0113ProfileTests(unittest.TestCase):
    def test_profile_is_versioned_and_has_an_explicit_native_opt_out(self):
        namespace = {
            "COMPATIBILITY_PROFILE_V0113": "open-webui-v0.11.3",
            "COMPATIBILITY_PROFILE_NATIVE": "native",
        }
        functions = extracted_functions(
            MAIN,
            {"compatibility_profile"},
            namespace,
        )
        profile = functions["compatibility_profile"]
        self.assertEqual(profile({}), "open-webui-v0.11.3")
        self.assertEqual(
            profile({"OPENATELIER_COMPATIBILITY_PROFILE": "native"}), "native"
        )
        self.assertEqual(
            profile({"OPENATELIER_COMPATIBILITY_PROFILE": "unknown"}),
            "open-webui-v0.11.3",
        )
        main_source = MAIN.read_text(encoding="utf-8")
        self.assertIn('"compatibility_profile": COMPATIBILITY_PROFILE', main_source)
        self.assertIn(
            "compatibility_profile?: 'open-webui-v0.11.3' | 'native';",
            STORES.read_text(encoding="utf-8"),
        )

    def test_legacy_notes_setting_controls_router_and_frontend_signal(self):
        functions = extracted_functions(
            MAIN,
            {"environment_flag_enabled", "notes_compat_enabled"},
            {},
        )
        enabled = functions["notes_compat_enabled"]
        self.assertTrue(enabled({}))
        for value in ("0", "false", "False", "no", "off"):
            self.assertFalse(enabled({"ENABLE_NOTES": value}), value)
        self.assertTrue(enabled({"ENABLE_NOTES": "true"}))
        self.assertFalse(
            enabled(
                {
                    "ENABLE_NOTES": "true",
                    "OPENATELIER_PRIVATE_NOTES_COMPAT": "0",
                }
            )
        )

    def test_assistant_message_keeps_the_v0113_descendant_contract(self):
        source = RESPONSE.read_text(encoding="utf-8")
        self.assertEqual(source.count("chat-assistant"), 1)
        self.assertIn("`group flex w-full message-${message.id}`", source)
        self.assertNotIn('id="message-{message.id}" class="chat-assistant', source)
        outer = source.index('id="message-{message.id}"')
        assistant = source.index('class="chat-assistant w-full min-w-full"')
        markdown = source.index("v0113Compatibility ? 'markdown-prose' : ''")
        self.assertLess(outer, assistant)
        self.assertLess(assistant, markdown)
        self.assertIn("'hidden shrink-0 ltr:mr-2 rtl:ml-2 @lg:flex mt-0.5'", source)
        self.assertIn("'shrink-0 ltr:mr-2 rtl:ml-2 hidden @lg:flex mt-0.5'", source)
        self.assertIn('id="response-message-{message.id}"', source)

    def test_message_reveal_hooks_apply_to_timestamp_and_actions(self):
        response = RESPONSE.read_text(encoding="utf-8")
        user = USER_MESSAGE.read_text(encoding="utf-8")
        self.assertIn("'group flex w-full user-message'", user)
        for source in (response, user):
            self.assertIn('message-timestamp hover-reveal', source)
            self.assertNotIn("invisible group-hover:visible", source)
        css = APP_CSS.read_text(encoding="utf-8")
        self.assertIn(".group:focus-within .hover-reveal", css)

    def test_sidebar_contract_keeps_selected_folders_heading_and_brand_order(self):
        sidebar = SIDEBAR.read_text(encoding="utf-8")
        chat_item = CHAT_ITEM.read_text(encoding="utf-8")
        folder = FOLDER.read_text(encoding="utf-8")
        self.assertIn('class:selected={id === $chatId}', chat_item)
        self.assertIn("name={$i18n.t('Chats')}", sidebar)
        self.assertIn("name={$i18n.t('Folders')}", sidebar)
        self.assertIn("onAdd={() => createFolder()}", sidebar)
        self.assertNotIn("invisible group-hover:visible", folder)
        self.assertIn("{#if name}", folder)
        self.assertIn(
            'id="sidebar-webui-name" class="self-center font-medium', sidebar
        )
        self.assertNotIn('id="sidebar-webui-name" class="sr-only"', sidebar)

    def test_legacy_header_hides_native_secondary_controls_as_one_profile(self):
        source = NAVBAR.read_text(encoding="utf-8")
        self.assertIn(
            "$: v0113Compatibility = $config?.compatibility_profile !== 'native';",
            source,
        )
        self.assertIn("{#if !v0113Compatibility && $temporaryChatEnabled}", source)
        self.assertIn(
            "{#if !v0113Compatibility && !$user?.permissions?.chat?.temporary_enforced",
            source,
        )
        self.assertIn("{#if !v0113Compatibility}", source)
        self.assertIn("<Tooltip content={$i18n.t('Controls')}>", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
