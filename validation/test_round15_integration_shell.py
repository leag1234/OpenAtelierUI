#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source contracts for the Round 15 integration-shell regressions."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MESSAGE_INPUT = ROOT / "src/lib/components/chat/MessageInput.svelte"
RESPONSE = ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
CHAT_NAVBAR = ROOT / "src/lib/components/chat/Navbar.svelte"
SIDEBAR = ROOT / "src/lib/components/layout/Sidebar.svelte"
FRENCH = ROOT / "src/lib/i18n/locales/fr-FR/translation.json"


class Round15IntegrationShellTests(unittest.TestCase):
    def test_composer_keeps_appended_children_in_block_flow_without_dark_band(self):
        source = MESSAGE_INPUT.read_text(encoding="utf-8")
        self.assertIn(
            'class="w-full flex flex-col items-stretch gap-1.5"', source
        )
        self.assertIn('class="w-full min-w-0 bg-transparent"', source)
        self.assertNotIn("'bg-white dark:bg-gray-900'", source)

    def test_each_response_exposes_one_assistant_hook_and_markdown_descendant(self):
        source = RESPONSE.read_text(encoding="utf-8")
        self.assertEqual(source.count("chat-assistant"), 1)
        outer = source.index('class="chat-assistant flex w-full')
        markdown = source.index(
            'class="chat-message-body w-full min-w-full markdown-prose"'
        )
        self.assertLess(outer, markdown)
        self.assertNotIn('class="chat-{message.role}', source)

    def test_sidebar_keeps_folders_create_action_and_single_line_branding(self):
        source = SIDEBAR.read_text(encoding="utf-8")
        self.assertIn('id="folders-section"', source)
        self.assertIn("name={$i18n.t('Folders')}", source)
        self.assertIn("onAdd={() => createFolder()}", source)
        self.assertIn(
            'id="sidebar-branding-row" class="flex min-w-0 items-center gap-1 whitespace-nowrap"',
            source,
        )
        french = json.loads(FRENCH.read_text(encoding="utf-8"))
        self.assertEqual(french["Folders"], "Dossiers")

    def test_phone_header_reserves_the_primary_row_for_title_and_primary_actions(self):
        source = CHAT_NAVBAR.read_text(encoding="utf-8")
        self.assertIn('id="sidebar-toggle-button"', source)
        self.assertIn('id="new-chat-button"', source)
        for element_id in ("temporary-chat-button", "chat-context-menu-button"):
            start = source.index(f'id="{element_id}"')
            context = source[max(0, start - 350) : start + 350]
            self.assertIn("hidden", context, element_id)
            self.assertIn("md:flex", context, element_id)
        controls = source.index("await showControls.set")
        controls_context = source[max(0, controls - 300) : controls]
        self.assertIn("hidden", controls_context)
        self.assertIn("md:flex", controls_context)
        self.assertIn('class="truncate text-left" title={title}', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
