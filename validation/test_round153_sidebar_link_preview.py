# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured native sidebar link-preview contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SIDEBAR = (ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte").read_text()


class SidebarLinkPreviewContractTests(unittest.TestCase):
    def test_trigger_keeps_navigation_and_exports_observed_attributes(self) -> None:
        self.assertIn('id="bits-link-preview-{chat.id}"', SIDEBAR)
        self.assertIn('href="/c/{chat.id}"', SIDEBAR)
        self.assertIn('aria-haspopup="dialog"', SIDEBAR)
        self.assertIn('data-link-preview-trigger=""', SIDEBAR)
        self.assertIn("data-state={linkPreviewChatId === chat.id ? 'open' : 'closed'}", SIDEBAR)
        self.assertIn('role="button"', SIDEBAR)

    def test_trigger_state_matches_the_rendered_preview(self) -> None:
        self.assertIn('aria-expanded={linkPreviewChatId === chat.id}', SIDEBAR)
        self.assertIn('{#if linkPreviewChatId === chat.id}', SIDEBAR)
        self.assertIn('id="sidebar-chat-preview-{chat.id}"', SIDEBAR)
        self.assertIn('role="dialog"', SIDEBAR)
        self.assertIn('use:portalToBody', SIDEBAR)
        self.assertIn('class="fixed z-[100]', SIDEBAR)
        self.assertIn('style:left={`${linkPreviewLeft}px`}', SIDEBAR)

    def test_mouse_and_keyboard_interactions_are_bounded(self) -> None:
        self.assertIn('on:mouseenter={() => openLinkPreview(chat.id)}', SIDEBAR)
        self.assertIn('on:mouseleave={() => closeLinkPreview(chat.id)}', SIDEBAR)
        self.assertIn('on:focus={() => openLinkPreview(chat.id, 0)}', SIDEBAR)
        self.assertIn("if (event.key === 'Escape') closeLinkPreview(chat.id, 0);", SIDEBAR)
        self.assertIn('const closeLinkPreview = (id: string, delay = 120)', SIDEBAR)

    def test_pending_preview_timer_is_destroyed_with_component(self) -> None:
        self.assertIn('if (linkPreviewTimer) clearTimeout(linkPreviewTimer);', SIDEBAR)


if __name__ == "__main__":
    unittest.main()
