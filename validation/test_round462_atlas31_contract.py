# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 31 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound31ContractTests(unittest.TestCase):
    def test_native_reconnect_confirms_stop_and_retries_transient_readiness(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        handler = chat[chat.index("reconnectHandler = async () => {") :]
        handler = handler[: handler.index("currentSocket?.on('connect', reconnectHandler)")]
        native = handler[handler.index("if (nativeCompatibility) {") :]
        self.assertIn("requestSavedChatStop(", native)
        self.assertIn("outcome.state === 'stopped' || outcome.state === 'already-finished'", native)
        self.assertIn("await sleep(250);", native)
        terminal = native[native.index("const stoppedHistory = stoppedHistorySnapshot") :]
        self.assertLess(terminal.index("await persistStoppedChat("), terminal.index("history = stoppedHistory;"))
        self.assertLess(terminal.index("history = stoppedHistory;"), terminal.index("taskIds = null;"))

    def test_stopped_snapshot_preserves_reasoning_status_before_terminal_marker(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        snapshot = chat[chat.index("const stoppedHistorySnapshot =") :]
        snapshot = snapshot[: snapshot.index("const stopResponse = async")]
        self.assertNotIn("status?.done !== false", snapshot)
        self.assertIn("status?.action !== 'response_stopped'", snapshot)
        self.assertLess(snapshot.index("...statuses"), snapshot.index("action: 'response_stopped'"))

    def test_native_response_renders_last_nonterminal_status(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        self.assertIn("const visibleResponseStatus = (value) =>", response)
        self.assertIn("find((status) => status?.action !== 'response_stopped')", response)
        self.assertIn("{@const status = visibleResponseStatus(message)}", response)

    def test_native_saved_chat_does_not_autofocus_composer(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn("if (!nativeCompatibility || !get(chatId))", chat)
        self.assertIn("export let chatId = '';", message_input)
        self.assertIn("if (v0113Compatibility || !chatId) chatInput?.focus();", message_input)
        self.assertIn("chatId={$chatId}", chat)


if __name__ == "__main__":
    unittest.main()
