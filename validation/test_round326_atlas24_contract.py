# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 24 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound24ContractTests(unittest.TestCase):
    def test_native_selection_toolbar_remains_mounted_while_inactive(self) -> None:
        toolbar = source("src/lib/components/chat/CurrentSelectionToolbar.svelte")
        self.assertNotIn("{#if visible}", toolbar)
        self.assertIn("style:display={visible ? 'block' : 'none'}", toolbar)
        self.assertIn('id={`floating-buttons-${chatId}-${messageId}`}', toolbar)
        self.assertEqual(toolbar.count('aria-label="Ask"'), 1)
        self.assertEqual(toolbar.count('aria-label="Explain"'), 1)

    def test_tabs_button_follows_saved_conversation_state(self) -> None:
        navbar = source("src/lib/components/chat/Navbar.svelte")
        self.assertEqual(
            navbar.count('{#if showNativeTabs}<div class="flex">'),
            2,
        )

    def test_native_timestamps_use_hover_reveal_without_viewport_class(self) -> None:
        user = source("src/lib/components/chat/Messages/UserMessage.svelte")
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertIn(
            'class="hover-reveal mr-1 text-[0.6875rem] tabular-nums',
            user,
        )
        self.assertIn(
            'class="hover-reveal ml-1 shrink-0 whitespace-nowrap',
            response,
        )

    def test_current_edit_and_profile_images_expose_accessible_attributes(self) -> None:
        user = source("src/lib/components/chat/Messages/UserMessage.svelte")
        profile = source("src/lib/components/chat/Messages/ProfileImage.svelte")
        edit_block = user[user.index("edit-user-message-button") :]
        edit_block = edit_block[: edit_block.index("</button>")]
        self.assertIn("aria-hidden={!v0113Compatibility", edit_block)
        self.assertIn("stroke={!v0113Compatibility ? 'currentColor' : undefined}", edit_block)
        self.assertIn('alt="profile"', profile)

    def test_current_attachment_and_sidebar_omit_extra_wrappers_and_loaders(self) -> None:
        attachment = source("src/lib/components/common/FileItemCurrent.svelte")
        sidebar = source("src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte")
        self.assertIn("spreadsheet ? 'flex' : 'flex justify-center text-center'", attachment)
        self.assertNotIn("responseStreaming", sidebar)
        self.assertNotIn("Loading...", sidebar)

    def test_native_regenerate_keeps_hidden_hook_and_accessible_menu_trigger(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        native = response[response.index('class="hidden regenerate-response-button"') :]
        native = native[: native.index("{#if siblings.length > 1}")]
        self.assertIn('class="hidden regenerate-response-button"', native)
        self.assertIn("use:nativeMenuTriggerContract={{", native)
        self.assertNotIn("transition regenerate-response-button", native)

    def test_loaded_pending_chat_starts_task_reconciliation(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn(
            "if (taskIds?.length && reconnectHandler) void reconnectHandler();",
            chat,
        )
        self.assertIn("for (let attempt = 0; attempt < 30; attempt += 1)", chat)
        self.assertIn("if (!activeTasks.length)", chat)


if __name__ == "__main__":
    unittest.main()
