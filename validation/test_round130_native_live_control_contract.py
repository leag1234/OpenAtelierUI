# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for native live-control accessibility contracts."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text(encoding="utf-8")
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(
    encoding="utf-8"
)
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text(encoding="utf-8")
SIDEBAR = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")


class NativeLiveControlContractTests(unittest.TestCase):
    def test_empty_chat_retains_polite_status_region(self) -> None:
        self.assertIn(
            '<div class="sr-only" aria-live="polite" aria-atomic="true"></div>',
            CHAT,
        )

    def test_native_stop_control_does_not_export_legacy_identity(self) -> None:
        self.assertIn(
            "id={v0113Compatibility ? 'stop-response-button' : undefined}", INPUT
        )
        self.assertIn(
            "title={v0113Compatibility ? $i18n.t('Stop') : undefined}", INPUT
        )
        self.assertIn("const stopLabel = $i18n.t('Stop');", SIDEBAR)
        self.assertIn("button.getAttribute('aria-label') === stopLabel", SIDEBAR)

    def test_native_regenerate_exposes_observed_popup_state(self) -> None:
        native = RESPONSE.split('{:else}', 1)[-1]
        self.assertIn('aria-label={$i18n.t(\'Regenerate\')}', native)
        self.assertIn('aria-haspopup="true"', native)
        self.assertIn('aria-expanded="false"', native)


if __name__ == "__main__":
    unittest.main()
