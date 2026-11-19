# SPDX-License-Identifier: BSD-3-Clause
"""Focused regressions derived from the Atlas Round 22 black-box report."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class AtlasRound22ContractTests(unittest.TestCase):
    def test_loaded_chat_reconciles_stale_tasks_with_persisted_pending_state(self) -> None:
        chat = source("src/lib/components/chat/Chat.svelte")
        helper = source("src/lib/utils/chat-session.js")
        self.assertIn("reconcileLoadedChatTaskState(history, taskRes)", chat)
        self.assertIn("pendingReplyIds.length > 0", helper)
        self.assertIn("taskIds: keepPending ? activeTaskIds : null", helper)
        self.assertIn("status.action === 'response_stopped'", helper)
        self.assertIn("isPendingAssistantReply(message)", chat)

    def test_native_stop_control_requires_a_pending_current_response(self) -> None:
        message_input = source("src/lib/components/chat/MessageInput.svelte")
        self.assertIn(
            ": isPendingAssistantReply(history.messages[history.currentId])",
            message_input,
        )

    def test_native_user_bubble_keeps_wrapper_but_actions_remain_direct(self) -> None:
        user_message = source("src/lib/components/chat/Messages/UserMessage.svelte")
        self.assertIn(
            "<MessageContentWrapper wrapped={!v0113Compatibility}>",
            user_message,
        )
        self.assertIn(
            "<MessageContentWrapper wrapped={v0113Compatibility}>",
            user_message,
        )

    def test_native_answer_actions_use_observed_inner_flex_group(self) -> None:
        response = source("src/lib/components/chat/Messages/ResponseMessage.svelte")
        group = source("src/lib/components/chat/Messages/ActionRowGroup.svelte")
        self.assertIn("<ActionRowGroup wrapped={!v0113Compatibility}>", response)
        self.assertIn('<div class="flex"><slot /></div>', group)
        self.assertNotIn("isLastMessage ? 'visible' : 'hover-reveal'", response)
        self.assertGreaterEqual(response.count("hover-reveal inline-flex size-7"), 9)


if __name__ == "__main__":
    unittest.main()
