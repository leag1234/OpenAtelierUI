#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Contracts for the regenerated current interruption and expanded sidebar."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
CURRENT_SIDEBAR = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text(encoding="utf-8")
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text(encoding="utf-8")
CONTENT = (
    ROOT / "src/lib/components/chat/Messages/ContentRenderer.svelte"
).read_text(encoding="utf-8")


class CurrentInterruptionSidebarTests(unittest.TestCase):
    def test_native_expanded_sidebar_uses_regenerated_current_component(self) -> None:
        self.assertIn("latestCompatibility && $showSidebar", SIDEBAR)
        self.assertIn("expanded={true}", SIDEBAR)
        self.assertIn("id={expanded ? 'sidebar' : undefined}", CURRENT_SIDEBAR)
        self.assertIn("on:click={() => showSidebar.set(false)}", CURRENT_SIDEBAR)
        self.assertIn("aria-label={$i18n.t('Close navigation')}", CURRENT_SIDEBAR)
        self.assertIn('style="display: none;"', CURRENT_SIDEBAR)

    def test_folders_control_has_bounded_expanded_state_and_create_action(self) -> None:
        self.assertIn("let foldersExpanded = false", CURRENT_SIDEBAR)
        self.assertIn("aria-expanded={foldersExpanded}", CURRENT_SIDEBAR)
        self.assertIn("onCreateFolder", CURRENT_SIDEBAR)
        self.assertIn("folderItems: Record<string, any>", CURRENT_SIDEBAR)
        self.assertIn('class="w-full px-1.5"', CURRENT_SIDEBAR)

    def test_search_control_filters_live_chat_rows_and_has_escape_fallback(self) -> None:
        self.assertIn("let searchOpen = false", CURRENT_SIDEBAR)
        self.assertIn("let searchQuery = ''", CURRENT_SIDEBAR)
        self.assertIn("aria-label={$i18n.t('Search Chats')}", CURRENT_SIDEBAR)
        self.assertIn("{#each visibleChats as chat, index}", CURRENT_SIDEBAR)
        self.assertIn("event.key === 'Escape'", CURRENT_SIDEBAR)

    def test_current_interruption_hides_internal_status_and_compacts_paragraph(self) -> None:
        self.assertIn("status?.action !== 'response_stopped'", RESPONSE)
        self.assertIn("currentCompactParagraphs={!v0113Compatibility", RESPONSE)
        self.assertIn("export let currentCompactParagraphs = false", CONTENT)
        self.assertIn("{currentCompactParagraphs}", CONTENT)

    def test_interrupted_first_turn_gets_a_persisted_fallback_title(self) -> None:
        self.assertIn("const interruptedTitle =", CHAT)
        self.assertIn("firstUserMessage?.content?.replace", CHAT)
        self.assertIn("interruptedTitle ? { title: interruptedTitle }", CHAT)


if __name__ == "__main__":
    unittest.main()
