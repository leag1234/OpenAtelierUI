# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 32 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound32ContractTests(unittest.TestCase):
    def test_native_reconnect_uses_full_readiness_budget(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        handler = chat[chat.index("reconnectHandler = async () => {") :]
        handler = handler[: handler.index("currentSocket?.on('connect', reconnectHandler)")]
        native = handler[handler.index("if (nativeCompatibility) {") :]
        self.assertIn("for (let attempt = 0; attempt < 480; attempt += 1)", native)
        self.assertIn("await sleep(250);", native)
        terminal = native[native.index("const stoppedHistory = stoppedHistorySnapshot") :]
        self.assertLess(terminal.index("await persistStoppedChat("), terminal.index("taskIds = null;"))

    def test_legacy_stopped_view_keeps_last_meaningful_status(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        visible = response[response.index("const visibleResponseStatus =") :]
        visible = visible[: visible.index("interface MessageType")]
        self.assertIn("status?.action !== 'response_stopped'", visible)
        self.assertIn("statuses.at(-1)", visible)
        self.assertNotIn("if (v0113Compatibility) return statuses.at(-1)", visible)

    def test_saved_native_route_suppresses_mount_autofocus(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn(
            "suppressInitialFocus={nativeCompatibility && Boolean(chatIdProp)}", chat
        )
        self.assertIn("export let suppressInitialFocus = false;", message_input)
        self.assertIn(
            "if (v0113Compatibility || (!chatId && !suppressInitialFocus)) chatInput?.focus();",
            message_input,
        )


if __name__ == "__main__":
    unittest.main()
