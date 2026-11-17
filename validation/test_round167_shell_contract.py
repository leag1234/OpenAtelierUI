# SPDX-License-Identifier: BSD-3-Clause
"""Focused source contracts for the Round 166 native shell residuals."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
PENCIL = (ROOT / "src/lib/components/icons/PencilSquare.svelte").read_text(encoding="utf-8")


class Round167ShellContract(unittest.TestCase):
    def test_empty_initial_chat_page_closes_pagination(self) -> None:
        self.assertIn("allChatsLoaded = initialChats.length === 0;", SIDEBAR)

    def test_collapsed_desktop_trigger_contract_is_stable(self) -> None:
        self.assertIn('id="sidebar-open-button"', SIDEBAR)
        self.assertIn('pathStroke={true}', SIDEBAR)

    def test_collapsed_user_menu_keeps_behavior_without_generated_attributes(self) -> None:
        self.assertIn(
            'use:nativeMenuTriggerContract={{ enabled: true, removeGeneratedId: true }}',
            SIDEBAR,
        )
        self.assertIn("aria-label={$i18n.t('Open User Profile Menu')}", SIDEBAR)

    def test_native_notes_and_workspace_icons_expose_observed_path_attributes(self) -> None:
        self.assertIn(
            '<svg aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke-width="1.5" class="size-4">',
            SIDEBAR,
        )
        self.assertGreaterEqual(SIDEBAR.count('stroke="currentColor" stroke-width="1.5"'), 3)

    def test_pencil_can_move_stroke_to_path_without_changing_other_callers(self) -> None:
        self.assertIn("export let pathStroke = false;", PENCIL)
        self.assertIn("stroke={pathStroke ? undefined : 'currentColor'}", PENCIL)
        self.assertIn("stroke={pathStroke ? 'currentColor' : undefined}", PENCIL)


if __name__ == "__main__":
    unittest.main()
