# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 27 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound27ContractTests(unittest.TestCase):
    def test_panel_tab_bar_remains_mounted_on_new_chat(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertNotIn("{#if !nativeCompatibility || history.currentId}", chat)
        self.assertEqual(chat.count("<ChatControls"), 1)

    def test_native_model_context_image_omits_alt_attribute(self) -> None:
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn("alt={v0113Compatibility ? 'model profile' : undefined}", message_input)

    def test_selection_toolbar_uses_a_live_desktop_media_query(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("window.matchMedia('(min-width: 640px)')", chat)
        self.assertIn("selectionToolbarMediaQuery.addEventListener('change'", chat)
        self.assertIn("selectionToolbarMediaQuery.removeEventListener('change'", chat)
        self.assertIn(
            "{#if nativeCompatibility && history.currentId && selectionToolbarDesktop}",
            chat,
        )


if __name__ == "__main__":
    unittest.main()
