# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks derived from the Atlas Round 20 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text()
MESSAGE = (ROOT / "src/lib/components/chat/Messages/Message.svelte").read_text()
RESPONSE = (ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte").read_text()


class AtlasRound20ContractTests(unittest.TestCase):
    def test_native_main_panel_exports_observed_identity_and_relationships(self) -> None:
        self.assertIn("'chat-panel-main-chat-view'", CHAT)
        self.assertIn("role={nativeCompatibility ? 'tabpanel' : undefined}", CHAT)
        self.assertIn("aria-labelledby={nativeCompatibility ? 'chat-panel-main-chat'", CHAT)
        self.assertIn("data-panel-group={nativeCompatibility ? 'default' : undefined}", CHAT)

    def test_native_mobile_and_desktop_tabs_target_the_main_panel(self) -> None:
        self.assertGreaterEqual(CONTROLS.count('data-panel-tab="main-chat"'), 2)
        self.assertGreaterEqual(CONTROLS.count('aria-controls="chat-panel-main-chat-view"'), 2)

    def test_native_messages_are_marked_virtualized(self) -> None:
        self.assertIn("? `message-virtualized flex flex-col", MESSAGE)

    def test_native_status_is_a_text_control_without_legacy_loader(self) -> None:
        self.assertIn('class="absolute inset-0"', RESPONSE)
        self.assertIn('aria-expanded="false"', RESPONSE)
        self.assertIn("'text-[0.90625rem]'", RESPONSE)
        self.assertIn("status?.done === false && v0113Compatibility", RESPONSE)
        self.assertIn("{:else if v0113Compatibility}", RESPONSE)

    def test_native_usage_relationship_and_icon_contract(self) -> None:
        self.assertIn('id="usage-{message.id}"', RESPONSE)
        self.assertIn("`usage-${message.id}`", RESPONSE)
        self.assertIn("class={v0113Compatibility ? 'w-4 h-4' : 'size-4'}", RESPONSE)

    def test_native_answer_timestamp_is_not_permanently_rendered(self) -> None:
        self.assertNotIn("message.timestamp && message.done && !v0113Compatibility", RESPONSE)


if __name__ == "__main__":
    unittest.main()
