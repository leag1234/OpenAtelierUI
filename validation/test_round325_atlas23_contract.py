# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 23 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound23ContractTests(unittest.TestCase):
    def test_native_action_row_has_no_global_flex_wrapper(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertNotIn("ActionRowGroup", response)
        self.assertIn("isLastMessage ? 'visible' : 'hover-reveal'", response)
        self.assertGreaterEqual(response.count("nativeActionVisibility"), 10)

    def test_native_virtualizes_only_turns_older_than_the_current_pair(self) -> None:
        message = source("src/lib/components/chat/Messages/Message.svelte")
        self.assertIn(
            "currentTurnUserId = history.messages[history.currentId]?.parentId ?? null",
            message,
        )
        self.assertIn("messageId !== history.currentId", message)
        self.assertIn("messageId !== currentTurnUserId", message)
        self.assertNotIn("currentMessage?.done !== true", message)

    def test_selection_toolbar_stays_mounted_but_hidden_without_a_selection(self) -> None:
        toolbar = source("src/lib/components/chat/CurrentSelectionToolbar.svelte")
        self.assertNotIn("{#if visible}", toolbar)
        self.assertIn("style:display={visible ? 'block' : 'none'}", toolbar)

    def test_native_user_timestamp_retains_hover_reveal_without_viewport_hiding(self) -> None:
        user_message = source("src/lib/components/chat/Messages/UserMessage.svelte")
        self.assertIn(
            'class="hover-reveal mr-1 text-[0.6875rem] tabular-nums',
            user_message,
        )
        self.assertEqual(user_message.count("hover-reveal hidden @lg:block"), 1)

    def test_native_home_controls_follow_the_observed_user_role(self) -> None:
        controls = source("src/lib/components/chat/ChatControls.svelte")
        navbar = source("src/lib/components/chat/Navbar.svelte")
        self.assertIn("disabled={$user?.role !== 'admin'}", controls)
        self.assertIn("{#if $user?.role === 'admin'}", controls)
        self.assertIn('aria-label="New tab"', controls)
        self.assertIn("{#if history.currentId}<div class=\"flex\">", navbar)

    def test_native_selected_model_heading_exposes_the_badge_anchor(self) -> None:
        placeholder = source("src/lib/components/chat/Placeholder.svelte")
        self.assertIn(
            '<span class="flex items-center"><span class="line-clamp-1">',
            placeholder,
        )

    def test_native_user_delete_action_uses_the_current_button_template(self) -> None:
        user_message = source("src/lib/components/chat/Messages/UserMessage.svelte")
        self.assertIn(
            "hover-reveal inline-flex size-7 shrink-0 items-center justify-center p-1.5",
            user_message,
        )
        self.assertIn("aria-label={$i18n.t('Delete')}", user_message)


if __name__ == "__main__":
    unittest.main()
