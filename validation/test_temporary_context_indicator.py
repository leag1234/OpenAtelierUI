#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source checks for the persistent temporary-chat Navbar indicator."""

from pathlib import Path
import unittest


BASE = Path(__file__).resolve().parents[1]
NAVBAR = BASE / "src/lib/components/chat/Navbar.svelte"


class TemporaryContextIndicatorTests(unittest.TestCase):
    def test_indicator_uses_existing_store_and_translation_in_model_area(self):
        source = NAVBAR.read_text(encoding="utf-8")
        model_area = source.index('<ModelSelector bind:selectedModels')
        indicator = source.index("{$i18n.t('Temporary Chat')}")
        control_area = source.index('id="chat-context-menu-button"')
        self.assertIn("temporaryChatEnabled,", source)
        self.assertLess(model_area, indicator)
        self.assertLess(indicator, control_area)
        self.assertIn('{#if $temporaryChatEnabled}', source[:indicator])

    def test_saved_header_does_not_show_temporary_indicator(self):
        source = NAVBAR.read_text(encoding="utf-8")
        indicator_block = source[source.index("{#if $temporaryChatEnabled}"):source.index("{/if}", source.index("{#if $temporaryChatEnabled}"))]
        self.assertIn("Temporary Chat", indicator_block)
        self.assertNotIn("chatId", indicator_block)


if __name__ == "__main__":
    unittest.main(verbosity=2)
