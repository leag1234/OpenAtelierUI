#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Observed native sidebar state and mobile geometry contracts."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
SHADOW = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")


class CurrentSidebarStateTests(unittest.TestCase):
    def test_mobile_overlay_and_hidden_new_chat_match_observed_shell(self) -> None:
        self.assertIn('style="opacity: 0.6; transition: none;"', SIDEBAR)
        self.assertIn('<button id="sidebar-new-chat-button" class="hidden"></button>', SIDEBAR)

    def test_expanded_mobile_sidebar_owns_the_full_observed_width(self) -> None:
        self.assertIn("'width: 350px; --sidebar-width: 350px;", SHADOW)
        self.assertIn("aria-hidden={expanded ? 'false' : 'true'}", SHADOW)
        self.assertIn("data-state={expanded ? 'true' : 'false'}", SHADOW)

    def test_sidebar_primary_controls_expose_observed_state_attributes(self) -> None:
        self.assertIn('data-inbox-trigger=""', SHADOW)
        self.assertIn('aria-controls="sidebar-notifications"', SHADOW)
        self.assertIn('aria-pressed="false"', SHADOW)
        self.assertIn('data-id="notes"', SHADOW)
        self.assertIn('data-id="workspace"', SHADOW)

    def test_chat_menu_identity_and_popup_state_are_stable(self) -> None:
        self.assertIn('id="sidebar-chat-menu-{chat.id}"', SHADOW)
        self.assertIn('aria-haspopup="true"', SHADOW)
        self.assertIn('aria-expanded="false"', SHADOW)


if __name__ == "__main__":
    unittest.main()
