#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Check accessible names on the specifically observed own controls."""

from __future__ import annotations

import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
CHAT_INPUT = BASE / "src/lib/components/chat/MessageInput.svelte"
RICH_INPUT = BASE / "src/lib/components/common/RichTextInput.svelte"
RESPONSE = BASE / "src/lib/components/chat/Messages/ResponseMessage.svelte"
GENERAL = BASE / "src/lib/components/chat/Settings/General.svelte"
SETTINGS = BASE / "src/lib/components/chat/SettingsModal.svelte"


class AccessibleControlsTests(unittest.TestCase):
    def test_composer_and_send_have_names_in_both_editor_modes(self):
        source = CHAT_INPUT.read_text(encoding="utf-8")
        rich = RICH_INPUT.read_text(encoding="utf-8")
        self.assertIn("ariaLabel={$i18n.t('Send a Message')}", source)
        self.assertIn("aria-label={$i18n.t('Send a Message')}", source)
        self.assertIn("aria-label={$i18n.t('Send message')}", source)
        self.assertIn("export let ariaLabel = ''", rich)
        self.assertIn("'aria-label': ariaLabel", rich)

    def test_response_icon_actions_have_names_without_tooltip_inference(self):
        source = RESPONSE.read_text(encoding="utf-8")
        for label in (
            "Edit",
            "Copy",
            "Read Aloud",
            "Good Response",
            "Bad Response",
            "Continue Response",
            "Regenerate",
        ):
            self.assertIn(f"aria-label={{$i18n.t('{label}')}}", source, label)

    def test_general_selectors_and_settings_close_have_names(self):
        general = GENERAL.read_text(encoding="utf-8")
        settings = SETTINGS.read_text(encoding="utf-8")
        self.assertIn("aria-label={$i18n.t('Theme')}", general)
        self.assertIn("aria-label={$i18n.t('Language')}", general)
        self.assertIn("aria-label={$i18n.t('Close')}", settings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
