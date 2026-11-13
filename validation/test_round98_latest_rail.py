# SPDX-License-Identifier: BSD-3-Clause
"""Static contract checks for the independently regenerated current-reference rail."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = ROOT / "src/lib/components/layout/Sidebar.svelte"
CHAT_CONTROLS = ROOT / "src/lib/components/chat/ChatControls.svelte"


class LatestCollapsedRailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sidebar = SIDEBAR.read_text(encoding="utf-8")
        cls.controls = CHAT_CONTROLS.read_text(encoding="utf-8")

    def test_native_rail_uses_observed_width_and_outer_contract(self) -> None:
        self.assertIn("w-[calc(42px*var(--app-text-scale,1))] shrink-0 pt-0.5 pb-1 px-1", self.sidebar)
        self.assertIn('role="presentation" class="flex flex-col flex-1 cursor-[e-resize]"', self.sidebar)

    def test_native_rail_preserves_direct_control_landmarks(self) -> None:
        self.assertIn('id="sidebar-open-button"', self.sidebar)
        self.assertIn('aria-label={$i18n.t(\'New Chat\')}', self.sidebar)
        self.assertIn('aria-label={$i18n.t(\'Search\')}', self.sidebar)

    def test_native_tab_overlay_starts_after_the_rail(self) -> None:
        self.assertIn('left: 42px; width: calc(100% - 42px);', self.controls)

    def test_v0113_profile_keeps_existing_48_pixel_rail(self) -> None:
        self.assertIn(": 'relative shrink-0 h-screen max-h-[100dvh] w-12'", self.sidebar)
        self.assertIn("latestCompatibility ? undefined : 'width:48px !important'", self.sidebar)
        self.assertIn('id="collapsed-navigation-rail"', self.sidebar)


if __name__ == "__main__":
    unittest.main()
