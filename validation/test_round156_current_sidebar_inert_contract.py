# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured current hidden-sidebar focus contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte").read_text()


class CurrentSidebarInertContractTests(unittest.TestCase):
    def test_hidden_sidebar_uses_the_native_inert_boundary(self) -> None:
        self.assertIn("inert={expanded ? undefined : true}", SIDEBAR)
        self.assertIn("aria-hidden={expanded ? 'false' : 'true'}", SIDEBAR)

    def test_descendants_do_not_export_unobserved_tabindex_attributes(self) -> None:
        self.assertNotIn("tabindex={expanded ? undefined : -1}", SIDEBAR)

    def test_visible_controls_keep_accessible_names_and_actions(self) -> None:
        for label in (
            "$i18n.t('New Chat')",
            "$i18n.t('Notifications')",
            "$i18n.t('Close navigation')",
            "$i18n.t('Notes')",
            "$i18n.t('Workspace')",
        ):
            self.assertIn(f"aria-label={{{label}}}", SIDEBAR)
        self.assertIn("on:click={() => showSidebar.set(false)}", SIDEBAR)


if __name__ == "__main__":
    unittest.main()
