#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Observed current-reference semantics shared by every chat state."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text(encoding="utf-8")
MESSAGE = (
    ROOT / "src/lib/components/chat/Messages/Message.svelte"
).read_text(encoding="utf-8")
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text(encoding="utf-8")
RICH_TEXT = (
    ROOT / "src/lib/components/common/RichTextInput.svelte"
).read_text(encoding="utf-8")


class CurrentSharedSemanticContractTests(unittest.TestCase):
    def test_current_chat_shell_matches_the_reference_presentation_role(self) -> None:
        self.assertIn("role={nativeCompatibility ? 'presentation' : undefined}", CHAT)

    def test_each_rendered_message_is_a_list_item(self) -> None:
        self.assertIn('<div\n\trole="listitem"', MESSAGE)

    def test_current_model_name_uses_the_reference_stable_identity(self) -> None:
        self.assertIn("'response-message-model-name'", RESPONSE)
        self.assertIn("`response-message-${message.id}`", RESPONSE)

    def test_message_editor_preserves_direction_and_multiline_semantics(self) -> None:
        self.assertIn("messageInput ? { 'aria-multiline': 'true' } : {}", RICH_TEXT)
        self.assertIn("dir={messageInput ? 'auto' : undefined}", RICH_TEXT)


if __name__ == "__main__":
    unittest.main()
