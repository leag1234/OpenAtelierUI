# SPDX-License-Identifier: BSD-3-Clause
"""Static contract checks for the independently regenerated application shell."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "src/routes/(app)/+layout.svelte"
PLACEHOLDER = ROOT / "src/lib/components/chat/Placeholder.svelte"
CHAT_NAVBAR = ROOT / "src/lib/components/chat/Navbar.svelte"
CHAT_CONTROLS = ROOT / "src/lib/components/chat/ChatControls.svelte"
CHAT = ROOT / "src/lib/components/chat/Chat.svelte"


class LatestApplicationShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = LAYOUT.read_text(encoding="utf-8")

    def test_skip_link_is_outside_the_application_frame(self) -> None:
        skip = self.source.index('href="#main-content"')
        frame = self.source.index('class="flex h-dvh w-full overflow-hidden"')
        self.assertLess(skip, frame)
        self.assertIn("focus:z-[9999]", self.source[skip:frame])

    def test_frame_preserves_a_min_width_escape_for_chat_content(self) -> None:
        self.assertIn(
            '<div class="flex h-dvh w-full overflow-hidden">\n'
            '\t<div class="min-w-0 flex-1">\n'
            '\t\t<div class="app relative">',
            self.source,
        )

    def test_main_content_target_does_not_own_the_contents_layout(self) -> None:
        self.assertIn(
            '<main class="contents">\n'
            '\t\t\t\t<div id="main-content" tabindex="-1" class="sr-only left-0 top-0"></div>',
            self.source,
        )
        self.assertNotIn('<main id="main-content"', self.source)

    def test_legacy_nested_skip_link_was_removed(self) -> None:
        self.assertEqual(self.source.count("Skip to main content"), 1)


class LatestHomePlaceholderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PLACEHOLDER.read_text(encoding="utf-8")

    def test_native_profile_uses_observed_latest_home_width(self) -> None:
        self.assertIn(
            "m-auto w-full max-w-[58rem] px-1 md:@3xl:px-20 translate-y-6 py-24 text-center",
            self.source,
        )

    def test_native_profile_uses_observed_heading_contract(self) -> None:
        self.assertIn(
            "flex flex-row justify-center gap-2.5 @sm:gap-3 w-fit px-5 max-w-xl",
            self.source,
        )
        self.assertIn(
            '<h2 class="text-2xl @sm:text-2xl line-clamp-1 flex items-center"',
            self.source,
        )

    def test_v0113_profile_keeps_its_existing_heading_contract(self) -> None:
        self.assertIn(
            '<div class="text-2xl line-clamp-1 flex items-center whitespace-nowrap"',
            self.source,
        )


class LatestChatNavbarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CHAT_NAVBAR.read_text(encoding="utf-8")

    def test_native_profile_uses_observed_hidden_chat_navbar(self) -> None:
        self.assertIn(
            "sticky top-0 z-30 w-full pb-0.5 flex flex-col items-center drag-region",
            self.source,
        )
        self.assertIn(
            '<div class="flex items-center w-full pl-1.5 pr-1 hidden">',
            self.source,
        )

    def test_native_profile_keeps_accessible_tab_control(self) -> None:
        self.assertIn("aria-label={$i18n.t('Tabs')}", self.source)
        self.assertIn('<rect x="4" y="4" width="16" height="16" rx="1.5">', self.source)


class LatestChatTabBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CHAT_CONTROLS.read_text(encoding="utf-8")

    def test_native_profile_exposes_observed_tab_bar_landmarks(self) -> None:
        self.assertIn('id="controls-container"', self.source)
        self.assertIn('data-panel-tab-bar=""', self.source)
        self.assertIn('data-panel-tab="main-chat"', self.source)
        self.assertIn('id="chat-panel-main-chat"', self.source)

    def test_tab_controls_remain_directly_operable(self) -> None:
        self.assertIn('aria-label="New tab"', self.source)
        self.assertIn('id="temporary-chat-button"', self.source)

    def test_tab_bar_does_not_render_beneath_the_collapsed_rail(self) -> None:
        self.assertIn('left: 48px; width: calc(100% - 48px);', self.source)

    def test_empty_chat_title_has_a_readable_fallback(self) -> None:
        source = CHAT.read_text(encoding="utf-8")
        self.assertIn("title={$chatTitle || $i18n.t('New Chat')}", source)


if __name__ == "__main__":
    unittest.main()
