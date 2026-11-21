# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 29 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound29ContractTests(unittest.TestCase):
    def test_stop_control_stays_until_terminal_marker_is_durable(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        stop = chat[chat.index("const stopResponse = async () => {") :]
        stop = stop[: stop.index("const submitMessage = async")]
        self.assertLess(stop.index("requestSavedChatStop("), stop.index("stoppedHistorySnapshot("))
        self.assertLess(stop.index("await persistStoppedChat("), stop.index("history = stoppedHistory;"))
        self.assertLess(stop.index("history = stoppedHistory;"), stop.index("taskIds = null;"))
        self.assertNotIn("const optimisticallyStopped", stop)

    def test_terminal_snapshot_removes_in_progress_statuses(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        snapshot = chat[chat.index("const stoppedHistorySnapshot =") :]
        snapshot = snapshot[: snapshot.index("const stopResponse = async")]
        self.assertIn("filter((status) => status?.done !== false)", snapshot)
        self.assertIn("delete message.status", snapshot)
        self.assertIn("action: 'response_stopped'", snapshot)

    def test_interrupted_phone_turn_cannot_mount_selection_toolbar(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        self.assertIn("!hasStoppedResponseStatus(history.messages[history.currentId])", chat)

    def test_native_phone_attachment_does_not_force_composer_focus(self) -> None:
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn("if (v0113Compatibility || !$mobile) chatInput?.focus();", message_input)

    def test_folder_toggle_releases_composer_focus(self) -> None:
        sidebar = source("src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte")
        toggle = sidebar[sidebar.index("const toggleFolders =") :]
        toggle = toggle[: toggle.index("onMount(")]
        self.assertIn("document.getElementById('chat-input')?.blur();", toggle)


if __name__ == "__main__":
    unittest.main()
