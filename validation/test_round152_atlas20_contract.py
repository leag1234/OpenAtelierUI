# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks derived from the Atlas Round 20 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text()
MESSAGE = (ROOT / "src/lib/components/chat/Messages/Message.svelte").read_text()
RESPONSE = (ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte").read_text()
CURRENT_STATUS = (
    ROOT / "src/lib/components/chat/Messages/ResponseStatusCurrent.svelte"
).read_text()
USER_MESSAGE = (ROOT / "src/lib/components/chat/Messages/UserMessage.svelte").read_text()
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte").read_text()


class AtlasRound20ContractTests(unittest.TestCase):
    def test_native_main_panel_exports_observed_identity_and_relationships(self) -> None:
        self.assertIn("id={nativeCompatibility ? undefined : 'chat-pane'}", CHAT)
        self.assertIn("data-main-chat-pane={nativeCompatibility ? '' : undefined}", CHAT)
        self.assertNotIn("role={nativeCompatibility ? 'tabpanel' : undefined}", CHAT)
        self.assertNotIn("aria-labelledby={nativeCompatibility ? 'chat-panel-main-chat'", CHAT)
        self.assertNotIn("data-panel-group={nativeCompatibility ? 'default' : undefined}", CHAT)

    def test_native_mobile_and_desktop_tabs_target_the_main_panel(self) -> None:
        self.assertEqual(CONTROLS.count('data-panel-tab="main-chat"'), 1)
        self.assertEqual(CONTROLS.count('aria-controls="chat-container"'), 1)
        self.assertNotIn('aria-controls="chat-panel-main-chat-view"', CONTROLS)

    def test_native_messages_are_marked_virtualized(self) -> None:
        self.assertNotIn("message-virtualized", MESSAGE)

    def test_native_status_is_a_text_control_without_legacy_loader(self) -> None:
        self.assertIn('class="absolute inset-0"', CURRENT_STATUS)
        self.assertIn('aria-expanded="false"', CURRENT_STATUS)
        self.assertIn('class="text-[0.90625rem] flex flex-col w-full"', CURRENT_STATUS)
        self.assertIn("<ResponseStatusCurrent {status} />", RESPONSE)
        self.assertNotIn("Spinner", CURRENT_STATUS)

    def test_native_usage_relationship_and_icon_contract(self) -> None:
        self.assertIn('id="usage-{message.id}"', RESPONSE)
        self.assertIn("`usage-${message.id}`", RESPONSE)
        self.assertIn('class="w-4 h-4"', RESPONSE)
        self.assertIn('<svg class="size-4" aria-hidden="true"', RESPONSE)

    def test_native_answer_timestamp_is_not_permanently_visible(self) -> None:
        self.assertIn("message.timestamp && message.done && !v0113Compatibility", RESPONSE)
        self.assertIn(
            'class="hover-reveal ml-1 shrink-0 whitespace-nowrap text-[0.6875rem]',
            RESPONSE,
        )

    def test_phone_user_timestamp_and_sidebar_date_are_localized(self) -> None:
        self.assertIn(
            'class="hover-reveal mr-1 text-[0.6875rem]',
            USER_MESSAGE,
        )
        self.assertIn("{$i18n.t(chat.time_range ?? 'Today')}", SIDEBAR)
        self.assertNotIn("{chat.time_range ?? $i18n.t('Today')}", SIDEBAR)


if __name__ == "__main__":
    unittest.main()
