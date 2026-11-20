# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 25 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound25ContractTests(unittest.TestCase):
    def test_tabs_distinguish_fresh_empty_home_from_saved_new_chat(self) -> None:
        navbar = source("src/lib/components/chat/Navbar.svelte")
        self.assertIn(
            "Boolean(history.currentId) ||",
            navbar,
        )
        self.assertIn("selectedModels.filter((modelId)", navbar)
        self.assertEqual(navbar.count('{#if showNativeTabs}<div class="flex">'), 2)

    def test_selection_toolbar_uses_chat_and_message_identity_during_streaming(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        toolbar = source("src/lib/components/chat/CurrentSelectionToolbar.svelte")
        self.assertIn(
            "{#if nativeCompatibility && history.currentId && selectionToolbarDesktop}",
            chat,
        )
        self.assertIn("chatId={$chatId}", chat)
        self.assertIn("messageId={history.currentId}", chat)
        self.assertIn('id={`floating-buttons-${chatId}-${messageId}`}', toolbar)
        self.assertIn("style:left={visible ? `${left}px` : undefined}", toolbar)
        self.assertIn("style:top={visible ? `${top}px` : undefined}", toolbar)

    def test_native_message_actions_preserve_hooks_and_accessible_names(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        user = source("src/lib/components/chat/Messages/UserMessage.svelte")
        profile = source("src/lib/components/chat/Messages/ProfileImage.svelte")
        self.assertIn('class="hidden regenerate-response-button"', response)
        self.assertIn("use:nativeMenuTriggerContract={{", response)
        self.assertIn("$i18n.t('Duplicate conversation')", response)
        self.assertIn("aria-hidden={!v0113Compatibility ? 'true' : undefined}", user)
        self.assertIn("stroke-width={v0113Compatibility ? '2.3' : '1.5'}", user)
        self.assertIn('alt="profile"', profile)

    def test_attachment_wrapper_varies_by_file_family(self) -> None:
        attachment = source("src/lib/components/common/FileItemCurrent.svelte")
        self.assertIn("/\\.(csv|tsv|xls|xlsx|ods)$/i", attachment)
        self.assertIn("spreadsheet ? 'flex' : 'flex justify-center text-center'", attachment)


if __name__ == "__main__":
    unittest.main()
