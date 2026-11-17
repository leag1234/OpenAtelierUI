# SPDX-License-Identifier: BSD-3-Clause
"""Focused source contracts for the exact Round 164 residual families."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


class Round165FocusedResiduals(unittest.TestCase):
    def test_provider_response_identity_reaches_current_code_block_ids(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        renderer = source("src/lib/components/chat/Messages/ContentRenderer.svelte")
        self.assertIn("message.responseId = id", chat)
        self.assertIn("responseId={message.responseId ?? null}", response)
        self.assertIn("`${$chatId}-${id}-${responseId ?? id}`", renderer)

    def test_closed_current_controls_do_not_emit_an_unused_pane(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        self.assertIn("{#if !latestCompatibility || $showControls}", controls)
        self.assertIn('aria-controls="chat-panel-main-chat-view"', controls)

    def test_desktop_and_phone_sidebar_triggers_have_measured_contracts(self) -> None:
        sidebar = source("src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte")
        self.assertIn("`bits-c${visibleChats.length - index}`", sidebar)
        self.assertIn("aria-expanded={mobile ? undefined", sidebar)
        self.assertIn('style="overflow: hidden;"', sidebar)

    def test_current_code_toolbar_uses_phone_header_offset_only_on_phone(self) -> None:
        code = source("src/lib/components/chat/Messages/CodeBlock.svelte")
        self.assertIn("import { config, mobile } from '$lib/stores';", code)
        self.assertIn("${$mobile ? 'top-10' : 'top-0'}", code)

if __name__ == "__main__":
    unittest.main()
