# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 28 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound28ContractTests(unittest.TestCase):
    def test_phone_selection_toolbar_tracks_terminal_message_state(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("let nativeSelectionToolbarMounted = false", chat)
        self.assertIn("selectionToolbarDesktop ||", chat)
        self.assertIn("history.messages[history.currentId]?.done === true", chat)
        self.assertIn("!hasStoppedResponseStatus(history.messages[history.currentId])", chat)
        self.assertIn("{#if nativeSelectionToolbarMounted}", chat)

    def test_stop_invalidates_inflight_reconnect_inventory(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        stop = chat[chat.index("const stopResponse = async () => {") :]
        self.assertLess(
            stop.index("reconnectReconciliationEpoch += 1"),
            stop.index("requestSavedChatStop("),
        )

    def test_stopped_turn_hides_scroll_control_for_any_terminal_marker(self) -> None:
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn(
            "!hasStoppedResponseStatus(history.messages[history.currentId])",
            message_input,
        )

    def test_native_new_chat_uses_model_profile_endpoint(self) -> None:
        placeholder = source("src/lib/components/chat/Placeholder.svelte")
        self.assertIn(
            "`${WEBUI_API_BASE_URL}/models/model/profile/image?id=${model?.id}&lang=${$i18n.language}`",
            placeholder,
        )

    def test_sidebar_spinner_uses_measured_centering_wrapper(self) -> None:
        sidebar = source("src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte")
        self.assertIn('class="flex items-center justify-center size-3"', sidebar)


if __name__ == "__main__":
    unittest.main()
