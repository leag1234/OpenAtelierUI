#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Contracts for independently regenerated current phone states."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
NAVBAR = (ROOT / "src/lib/components/chat/Navbar.svelte").read_text(encoding="utf-8")
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text(encoding="utf-8")
MESSAGES = (ROOT / "src/lib/components/chat/Messages.svelte").read_text(encoding="utf-8")
CODE = (ROOT / "src/lib/components/chat/Messages/CodeBlock.svelte").read_text(encoding="utf-8")
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
SHADOW = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")


class CurrentPhoneTests(unittest.TestCase):
    def test_phone_header_restores_primary_navigation_and_title(self) -> None:
        self.assertIn("? 'sticky top-0 z-30 w-full pt-1.5 pb-1 -mb-12", NAVBAR)
        self.assertIn('id="sidebar-toggle-button"', NAVBAR)
        self.assertIn("aria-label={$i18n.t('Open Sidebar')}", NAVBAR)
        self.assertIn('class="min-w-0 truncate md:py-1 text-left', NAVBAR)
        self.assertIn("aria-label={$i18n.t('Tabs')}", NAVBAR)

    def test_desktop_tab_overlay_does_not_cover_phone_header(self) -> None:
        self.assertIn("{#if latestCompatibility && $mobile}", CONTROLS)
        self.assertIn('class="h-full w-full min-h-0 flex flex-col hidden"', CONTROLS)
        self.assertIn("{:else if latestCompatibility}", CONTROLS)

    def test_phone_messages_reserve_observed_header_space(self) -> None:
        self.assertIn("$mobile ? 'h-full flex pt-18'", MESSAGES)
        self.assertIn("'sticky top-0 left-0 right-0", CODE)

    def test_phone_sidebar_uses_observed_drawer_width(self) -> None:
        self.assertIn("mobile={$mobile}", SIDEBAR)
        self.assertIn("export let mobile = false", SHADOW)
        self.assertIn("'width: 350px; --sidebar-width: 350px;", SHADOW)
        self.assertIn("w-[var(--sidebar-width)]", SHADOW)
        self.assertIn("mobile ? 'px-0.75 pt-1 pb-1 -mb-2'", SHADOW)


if __name__ == "__main__":
    unittest.main()
