# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions for the exact Round 162 structural replay findings."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text()


class Round163HomogeneousStructuralContract(unittest.TestCase):
    def test_current_main_pane_keeps_phone_minimal_and_desktop_relationships(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("data-main-chat-pane={nativeCompatibility ? '' : undefined}", chat)
        self.assertIn("data-panel-group={nativeCompatibility && !$mobile ? 'default' : undefined}", chat)
        self.assertIn("role={nativeCompatibility && !$mobile ? 'tabpanel' : undefined}", chat)
        self.assertIn("aria-labelledby={nativeCompatibility && !$mobile ? 'chat-panel-main-chat' : undefined}", chat)

    def test_current_mobile_tab_strip_is_empty_and_desktop_tab_stays_accessible(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        self.assertIn(
            '<div role="tablist" tabindex="-1" data-swipe-no-gesture class="panel-tab-strip flex items-center gap-0.5 min-w-0 overflow-x-auto" aria-label="Tabs"></div>',
            controls,
        )
        self.assertEqual(controls.count('data-panel-tab="main-chat"'), 1)
        self.assertEqual(controls.count('aria-controls="chat-panel-main-chat-view"'), 1)
        self.assertIn('id="temporary-chat-button"', controls)
        self.assertIn('title="Split screen · Ctrl+\\\\"', controls)

    def test_current_message_and_interrupted_status_match_the_measured_shape(self) -> None:
        message = source("src/lib/components/chat/Messages/Message.svelte")
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertNotIn("message-virtualized", message)
        self.assertIn("{:else if status?.action !== 'response_stopped'}", response)

    def test_current_user_timestamp_is_available_at_phone_width(self) -> None:
        user = source("src/lib/components/chat/Messages/UserMessage.svelte")
        self.assertIn(
            'class="hover-reveal mr-1 text-[0.6875rem] tabular-nums',
            user,
        )

    def test_attachment_composer_and_home_gradient_follow_state(self) -> None:
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        navbar = source("src/lib/components/chat/Navbar.svelte")
        self.assertIn("${files.length > 0 ? '' : 'pt-2'}", message_input)
        self.assertIn("class=\"{history.currentId ? 'visible' : 'invisible'} bg-linear-to-b", navbar)

    def test_current_code_toolbar_uses_reference_sticky_offset(self) -> None:
        code = source("src/lib/components/chat/Messages/CodeBlock.svelte")
        self.assertIn("`sticky ${$mobile ? 'top-10' : 'top-0'} left-0 right-0", code)


if __name__ == "__main__":
    unittest.main()
