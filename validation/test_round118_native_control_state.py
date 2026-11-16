#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Native controls retain measured reference state without losing accessibility."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT_CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text(
    encoding="utf-8"
)
MODEL_SELECTOR = (
    ROOT / "src/lib/components/chat/ModelSelector/Selector.svelte"
).read_text(encoding="utf-8")
MESSAGE_INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(
    encoding="utf-8"
)
CHEVRON_DOWN = (ROOT / "src/lib/components/icons/ChevronDown.svelte").read_text(
    encoding="utf-8"
)


class NativeControlStateTests(unittest.TestCase):
    def test_panel_tab_strips_exclude_swipe_gestures(self) -> None:
        self.assertEqual(CHAT_CONTROLS.count("data-swipe-no-gesture"), 2)
        self.assertEqual(CHAT_CONTROLS.count('role="tablist"'), 2)

    def test_primary_native_model_selector_has_stable_id(self) -> None:
        self.assertIn(
            "nativeCompact && id === '0' ? 'model-selector-model-button'",
            MODEL_SELECTOR,
        )
        self.assertIn("`model-selector-${id}-button`", MODEL_SELECTOR)

    def test_decorative_control_icons_are_hidden_from_accessibility_tree(self) -> None:
        self.assertIn('aria-hidden="true"', CHEVRON_DOWN)
        input_menu = MESSAGE_INPUT[MESSAGE_INPUT.index('id="input-menu-button"') :]
        self.assertIn('aria-label={$i18n.t(\'More\')}', input_menu)
        self.assertIn('aria-hidden="true"', input_menu)
        self.assertIn('stroke-width="0"', input_menu)


if __name__ == "__main__":
    unittest.main()
