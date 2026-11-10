#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Contracts for named, non-nested saved-chat controls."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / "src/lib/components/layout/Navbar/Menu.svelte"
NAVBARS = (
    ROOT / "src/lib/components/layout/Navbar.svelte",
    ROOT / "src/lib/components/chat/Navbar.svelte",
)
USER_MESSAGE = ROOT / "src/lib/components/chat/Messages/UserMessage.svelte"


class SavedChatAccessibilityTests(unittest.TestCase):
    def test_context_menu_uses_single_named_as_child_trigger(self):
        menu = MENU.read_text(encoding="utf-8")
        self.assertIn("asChild", menu)
        self.assertIn("let:builder", menu)
        self.assertIn("<slot {builder} />", menu)
        for path in NAVBARS:
            source = path.read_text(encoding="utf-8")
            self.assertIn("<Menu\n", source)
            self.assertIn("let:builder", source)
            self.assertIn("use:builder.action", source)
            self.assertIn("aria-label={$i18n.t('Controls')}", source)

    def test_hover_message_actions_have_localized_names(self):
        source = USER_MESSAGE.read_text(encoding="utf-8")
        self.assertIn("aria-label={$i18n.t('Edit')}", source)
        self.assertIn("aria-label={$i18n.t('Copy')}", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
