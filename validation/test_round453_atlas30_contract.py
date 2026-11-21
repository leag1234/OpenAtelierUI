# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 30 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound30ContractTests(unittest.TestCase):
    def test_reconnect_inventory_cannot_resurrect_terminal_state(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        handler = chat[chat.index("reconnectHandler = async () => {") :]
        handler = handler[: handler.index("currentSocket?.on('connect', reconnectHandler)")]
        inventory = handler[handler.index("await getTaskIdsByChatId") :]
        self.assertLess(
            inventory.index("reconciliationEpoch !== reconnectReconciliationEpoch"),
            inventory.index("taskIds = activeTasks"),
        )

    def test_reconnecting_interruption_is_persisted_before_stop_clears(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        handler = chat[chat.index("reconnectHandler = async () => {") :]
        handler = handler[: handler.index("currentSocket?.on('connect', reconnectHandler)")]
        self.assertLess(
            handler.index("const stoppedHistory = stoppedHistorySnapshot(interruptedReplyIds)"),
            handler.index("await persistStoppedChat(currentChatId, stoppedHistory)"),
        )
        self.assertLess(
            handler.index("await persistStoppedChat(currentChatId, stoppedHistory)"),
            handler.index("history = stoppedHistory"),
        )
        terminal = handler[handler.index("const stoppedHistory = stoppedHistorySnapshot") :]
        self.assertLess(terminal.index("history = stoppedHistory"), terminal.index("taskIds = null"))

    def test_interrupted_phone_turn_cannot_mount_selection_toolbar(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("!hasStoppedResponseStatus(history.messages[history.currentId])", chat)

    def test_persisted_folder_expansion_releases_composer_focus(self) -> None:
        sidebar = source("src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte")
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn(
            "requestAnimationFrame(() => requestAnimationFrame(releaseComposerFocus))",
            sidebar,
        )
        self.assertIn(
            "localStorage.getItem('openatelierui-folders-expanded') !== 'true'",
            message_input,
        )
        self.assertIn("const focusComposerForCurrentState = () =>", source("src/lib/components/chat/Chat.svelte"))


if __name__ == "__main__":
    unittest.main()
