from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeSidebarControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
        ).read_text()

    def test_close_navigation_keeps_name_and_click_behavior(self) -> None:
        marker = "aria-label={$i18n.t('Close navigation')}"
        start = self.source.index(marker) - 90
        end = self.source.index("</button>", start)
        control = self.source[start:end]
        self.assertNotIn('type="button"', control)
        self.assertIn("on:click={() => showSidebar.set(false)}", control)

    def test_chat_disclosure_and_action_icons_are_decorative(self) -> None:
        self.assertIn(
            'd="m9 18 6-6-6-6" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"',
            self.source,
        )
        self.assertIn("aria-label={$i18n.t('Chat list actions')}", self.source)
        self.assertIn('aria-haspopup="menu"', self.source)
        self.assertIn('aria-expanded="false"', self.source)
        self.assertIn(
            'd="M7 7h10" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"',
            self.source,
        )

    def test_chat_menu_glyph_keeps_named_button_and_path_contract(self) -> None:
        self.assertIn('aria-label="Chat Menu"', self.source)
        self.assertEqual(
            self.source.count('fill="currentColor" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"'),
            3,
        )


if __name__ == "__main__":
    unittest.main()
