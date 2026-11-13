#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Static contracts for the independently regenerated current sidebar shadow."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
SHADOW = (ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte").read_text(
    encoding="utf-8"
)


class CurrentSidebarShadowTests(unittest.TestCase):
    def test_shadow_is_native_collapsed_desktop_only(self) -> None:
        self.assertIn("latestCompatibility && !$showSidebar && !$mobile", SIDEBAR)
        self.assertIn("<CurrentSidebarShadow webuiName={$WEBUI_NAME} />", SIDEBAR)

    def test_shadow_remains_inert_when_not_expanded(self) -> None:
        self.assertIn("aria-hidden={expanded ? undefined : 'true'}", SHADOW)
        self.assertIn("inert={expanded ? undefined : true}", SHADOW)
        self.assertIn("expanded ? (mobile ? '350px' : 'var(--sidebar-width)') : '0px'", SHADOW)
        self.assertIn("pointer-events-none", SHADOW)

    def test_observed_current_sections_are_present(self) -> None:
        for element_id in (
            "sidebar-notifications",
            "sidebar-new-chat-button",
            "sidebar-search-button",
            "pinned-menu-items-list",
            "sidebar-notes-button",
            "sidebar-workspace-button",
            "sidebar-folders-button",
            "sidebar-chats-button",
            "sidebar-chats-content",
        ):
            self.assertIn(f'id="{element_id}"', SHADOW)

    def test_notes_and_workspace_follow_feature_and_role_state(self) -> None:
        self.assertIn("$config?.features?.openatelier_private_notes", SHADOW)
        self.assertIn("$user?.role === 'admin'", SHADOW)


if __name__ == "__main__":
    unittest.main()
