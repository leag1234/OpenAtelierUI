from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeSidebarSvgContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            ROOT / "src/lib/components/layout/Sidebar/CurrentSidebarShadow.svelte"
        ).read_text()

    def test_notes_and_workspace_icons_place_stroke_on_paths(self) -> None:
        self.assertIn('d="M5 3h14v18H5z" stroke="currentColor"', self.source)
        self.assertIn('d="M4 4h6v6H4z" stroke="currentColor"', self.source)
        self.assertIn('d="M17 14v6" stroke="currentColor" stroke-width="1.5"', self.source)

    def test_folder_disclosure_and_create_icons_are_decorative(self) -> None:
        self.assertIn(
            'd="m9 18 6-6-6-6" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"',
            self.source,
        )
        self.assertIn(
            'd="M12 5v14m-7-7h14" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"',
            self.source,
        )
        self.assertGreaterEqual(
            self.source.count('xmlns="http://www.w3.org/2000/svg"'),
            6,
        )


if __name__ == "__main__":
    unittest.main()
