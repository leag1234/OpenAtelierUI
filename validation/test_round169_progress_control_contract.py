# SPDX-License-Identifier: BSD-3-Clause
"""Focused source contracts for the Round 169 native control residuals."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar.svelte").read_text(encoding="utf-8")
SHADOW = (
    ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
).read_text(encoding="utf-8")
CONTROLS = (ROOT / "src/lib/components/chat/ChatControls.svelte").read_text(encoding="utf-8")
NAVBAR = (ROOT / "src/lib/components/chat/Navbar.svelte").read_text(encoding="utf-8")


class Round169ProgressControlContract(unittest.TestCase):
    def test_native_resizer_exposes_reference_accessibility_contract(self) -> None:
        for marker in (
            'id="sidebar-resizer"',
            'role="separator"',
            'aria-orientation="vertical"',
            'aria-valuemin="220"',
            'aria-valuemax="480"',
            "aria-label={$i18n.t('Resize panel')}",
            "aria-valuenow={sidebarWidth}",
        ):
            self.assertIn(marker, SIDEBAR)
        self.assertIn("setSidebarWidth(event.clientX, 220, 480)", SIDEBAR)

    def test_chat_preview_ids_follow_newest_first_reference_order(self) -> None:
        marker = "`bits-c${visibleChats.length - index}`"
        self.assertEqual(SHADOW.count(marker), 2)

    def test_folder_expansion_survives_page_recreation(self) -> None:
        self.assertIn("const foldersExpandedStorageKey", SHADOW)
        self.assertIn("localStorage.getItem(foldersExpandedStorageKey) === 'true'", SHADOW)
        self.assertIn("localStorage.setItem(foldersExpandedStorageKey", SHADOW)
        self.assertIn('on:click={toggleFolders}', SHADOW)

    def test_panel_temporary_control_only_exists_without_active_chat(self) -> None:
        marker = '{#if !chatId}<div class="flex">'
        self.assertIn(marker, CONTROLS)
        self.assertLess(CONTROLS.index(marker), CONTROLS.index('id="temporary-chat-button"'))

    def test_hidden_desktop_header_retains_delete_contract(self) -> None:
        desktop = NAVBAR[NAVBAR.index('pl-1.5 pr-1 hidden') :]
        self.assertIn('id="delete-chat-button"', desktop)

    def test_active_tab_retains_chat_action_contract(self) -> None:
        self.assertIn('id="chat-context-menu-button"', CONTROLS)
        self.assertIn('use:builder.action', CONTROLS)
        self.assertIn('class="flex size-4 shrink-0 cursor-pointer', CONTROLS)


if __name__ == "__main__":
    unittest.main()
