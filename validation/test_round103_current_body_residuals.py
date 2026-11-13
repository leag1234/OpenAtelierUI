#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Static contracts for independently regenerated current body residuals."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
NAVBAR = (ROOT / "src/lib/components/chat/Navbar.svelte").read_text(encoding="utf-8")
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(encoding="utf-8")
INPUT_MENU = (ROOT / "src/lib/components/chat/MessageInput/InputMenu.svelte").read_text(
    encoding="utf-8"
)
MODEL = (ROOT / "src/lib/components/chat/ModelSelector.svelte").read_text(encoding="utf-8")
PLACEHOLDER = (ROOT / "src/lib/components/chat/Placeholder.svelte").read_text(encoding="utf-8")
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text(encoding="utf-8")


class CurrentBodyResidualTests(unittest.TestCase):
    def test_current_rail_keeps_observed_hidden_and_presence_nodes(self) -> None:
        self.assertIn('class="hidden" aria-hidden="true" tabindex="-1"', SIDEBAR)
        self.assertIn('class="absolute -bottom-0.5 -right-0.5"', SIDEBAR)
        self.assertIn('class="relative inline-flex size-2.5 rounded-full', SIDEBAR)

    def test_current_navbar_keeps_observed_below_header_anchor(self) -> None:
        self.assertIn('class="absolute top-full left-0 right-0 h-fit"', NAVBAR)

    def test_current_composer_keeps_hidden_input_and_compact_microphone(self) -> None:
        self.assertIn('id="camera-input"', INPUT_MENU)
        self.assertIn('class="hidden"', INPUT_MENU)
        self.assertIn('style="display: contents; cursor: pointer;"', INPUT_MENU)
        self.assertNotIn('{#if !v0113Compatibility}<span></span>{/if}', INPUT)
        self.assertIn('class="size-[1.125rem]"', INPUT)
        self.assertIn('<rect x="8" y="3" width="8" height="12" rx="4">', INPUT)

    def test_current_model_selector_has_no_separate_add_model_control(self) -> None:
        self.assertIn('{#if !nativeCompatibility}', MODEL)
        self.assertNotIn('class="mr-2 flex shrink-0 items-center justify-center', MODEL)

    def test_current_suggestion_and_empty_pane_residuals_are_explicit(self) -> None:
        self.assertIn('{#if !v0113Compatibility}<div class="mb-0.5"></div>{/if}', PLACEHOLDER)
        self.assertIn("class={latestCompatibility ? '' : ' z-10 '}", CONTROLS)


if __name__ == "__main__":
    unittest.main()
