# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the independently regenerated current chat states."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text(encoding="utf-8")
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text(
    encoding="utf-8"
)
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(
    encoding="utf-8"
)
SIDEBAR = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")
TOOLBAR = (
    ROOT / "src/lib/components/chat/CurrentSelectionToolbar.svelte"
).read_text(encoding="utf-8")


class CurrentMessageStructureTests(unittest.TestCase):
    def test_sidebar_shadow_uses_live_chat_state(self) -> None:
        self.assertIn("$: visibleChats = ($chats ?? []).filter", SIDEBAR)
        self.assertIn("{#each visibleChats as chat, index}", SIDEBAR)
        self.assertIn("$chatId === chat.id", SIDEBAR)
        self.assertNotIn("document.querySelectorAll('button[aria-label]')", SIDEBAR)
        self.assertNotIn("responseStreaming", SIDEBAR)
        self.assertIn('role="listitem"', SIDEBAR)

    def test_chat_announces_stream_state_and_mounts_completed_toolbar(self) -> None:
        self.assertIn('class="sr-only" aria-live="polite"', CHAT)
        self.assertIn("history.messages[history.currentId]?.done", CHAT)
        self.assertIn("<CurrentSelectionToolbar", CHAT)

    def test_selection_toolbar_is_interactive(self) -> None:
        self.assertIn("document.addEventListener('selectionchange'", TOOLBAR)
        self.assertIn("element?.closest('.chat-assistant')", TOOLBAR)
        self.assertIn("dispatch('prompt', { action, text: selectedText })", TOOLBAR)
        self.assertIn('aria-label="Ask"', TOOLBAR)
        self.assertIn('aria-label="Explain"', TOOLBAR)

    def test_current_controls_match_observed_current_tab_strip(self) -> None:
        current_block = CONTROLS.split("{:else if latestCompatibility}", 1)[1].split(
            "<SvelteFlowProvider>", 1
        )[0]
        self.assertIn('role="tablist" aria-label="Tabs" tabindex="-1"', current_block)
        self.assertIn('aria-controls="chat-container"', current_block)
        self.assertIn('id="temporary-chat-button"', current_block)
        self.assertIn('aria-label="Temporary Chat"', current_block)
        self.assertNotIn('aria-label="Close tab"', current_block)
        self.assertNotIn('id="chat-context-menu-button"', current_block)

    def test_current_streaming_button_and_composer_tail_match_current_shape(self) -> None:
        self.assertIn("rounded-full p-[0.3125rem]", INPUT)
        self.assertIn(
            '{#if !v0113Compatibility}<div class="mb-0.5"></div>{/if}',
            INPUT,
        )
        self.assertIn("{#if v0113Compatibility || atSelectedModel !== undefined", INPUT)


if __name__ == "__main__":
    unittest.main()
