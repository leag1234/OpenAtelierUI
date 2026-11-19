# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks derived from the Atlas Round 21 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound21ContractTests(unittest.TestCase):
    def test_native_tab_strip_does_not_add_a_new_tab_control(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        self.assertNotIn('aria-label="New tab"', controls)

    def test_native_response_keeps_observed_message_identity(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertIn(": `flex w-full message-${message.id}`", response)

    def test_native_user_actions_are_direct_chat_user_children(self) -> None:
        user_message = source("src/lib/components/chat/Messages/UserMessage.svelte")
        wrapper = source("src/lib/components/chat/Messages/MessageContentWrapper.svelte")
        self.assertIn("<MessageContentWrapper wrapped={v0113Compatibility}>", user_message)
        self.assertIn("{#if wrapped}", wrapper)
        self.assertIn('<div class="w-full"><slot /></div>', wrapper)
        self.assertIn("{:else}\n\t<slot />", wrapper)

    def test_native_answer_actions_follow_last_message_visibility(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertNotIn(": 'visible inline-flex", response)
        self.assertNotIn('class="visible inline-flex', response)
        self.assertNotIn("isLastMessage ? 'visible' : 'hover-reveal'", response)
        self.assertGreaterEqual(response.count("hover-reveal inline-flex size-7"), 9)
        self.assertIn('id="usage-{message.id}" hidden', response)

    def test_native_progress_and_interruption_mark_virtualized_messages(self) -> None:
        message = source("src/lib/components/chat/Messages/Message.svelte")
        self.assertIn("nativeVirtualized", message)
        self.assertIn("currentMessage?.done !== true", message)
        self.assertIn("action === 'response_stopped'", message)

    def test_native_chat_controls_match_disabled_split_and_menu_contract(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        self.assertIn("use:nativeMenuTriggerContract={true}", controls)
        self.assertIn("title={'Split screen · Ctrl+\\\\'}", controls)
        self.assertIn("\n\t\t\t\t\tdisabled\n", controls)

    def test_native_empty_state_omits_unobserved_controls_and_empty_list(self) -> None:
        navbar = source("src/lib/components/chat/Navbar.svelte")
        suggestions = source("src/lib/components/chat/Suggestions.svelte")
        sidebar = source("src/lib/components/layout/Sidebar.svelte")
        self.assertNotIn("nativeSidebarTooltip", navbar)
        self.assertIn("{#if history.currentId}<div class=\"flex\">", navbar)
        self.assertIn("{#if filteredPrompts.length > 0}", suggestions)
        self.assertIn('<button id="sidebar-new-chat-button" class="hidden"></button>', sidebar)

    def test_native_labels_and_empty_home_markup_follow_french_reference(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        placeholder = source("src/lib/components/chat/Placeholder.svelte")
        translations = source("src/lib/i18n/locales/fr-FR/translation.json")
        self.assertIn("aria-label={$i18n.t('Chat actions')}", controls)
        self.assertIn('"Chat actions": "Actions de conversation"', translations)
        self.assertIn('"Chat list actions": "Plus"', translations)
        native_heading = placeholder[placeholder.index('{:else}\n\t\t\t\t\t<h2') :]
        native_heading = native_heading[: native_heading.index('</h2>')]
        self.assertNotIn('<span', native_heading)

    def test_reconnect_clears_stale_stop_state_without_pending_reply(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        marker = "if (!pendingReplyIds.length) {"
        self.assertIn(marker, chat)
        block = chat[chat.index(marker) : chat.index(marker) + 100]
        self.assertIn("taskIds = null", block)

    def test_native_empty_answer_does_not_add_english_fallback(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertIn("message.done === true && v0113Compatibility", response)

    def test_current_attachment_icon_uses_observed_centering_wrapper(self) -> None:
        file_item = source("src/lib/components/common/FileItemCurrent.svelte")
        self.assertIn('class="flex justify-center text-center"', file_item)


if __name__ == "__main__":
    unittest.main()
