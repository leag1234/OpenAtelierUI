#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Contracts for the first Round 19 whole-element repair family."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SHADOW = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")


class CurrentContractFamilyTests(unittest.TestCase):
    def test_sidebar_visible_copy_uses_the_locale_store(self) -> None:
        self.assertIn("const i18n = getContext('i18n')", SHADOW)
        for key in ("New Chat", "Search", "Notes", "Workspace", "Folders", "Chats"):
            self.assertIn(f"{{$i18n.t('{key}')}}", SHADOW)
        self.assertIn("$i18n.t('Today')", SHADOW)
        for literal in (">New Chat<", ">Search<", ">Folders<", ">Chats<"):
            self.assertNotIn(literal, SHADOW)

    def test_primary_sidebar_links_match_observed_stable_attributes(self) -> None:
        self.assertIn('aria-label={$i18n.t(\'New Chat\')}', SHADOW)
        self.assertIn('aria-label={$i18n.t(\'Search\')}', SHADOW)
        self.assertGreaterEqual(SHADOW.count('draggable="false"'), 2)

    def test_section_buttons_bind_controls_and_expansion(self) -> None:
        self.assertIn('aria-controls="sidebar-folders-content"', SHADOW)
        self.assertIn('id="sidebar-folders-content"', SHADOW)
        self.assertIn('aria-expanded="true"', SHADOW)
        self.assertIn('aria-controls="sidebar-chats-content"', SHADOW)
        self.assertIn("const contentSlotAttribute = { slot: 'content' }", SHADOW)
        self.assertIn('{...contentSlotAttribute} id="sidebar-chats-content"', SHADOW)


if __name__ == "__main__":
    unittest.main()
