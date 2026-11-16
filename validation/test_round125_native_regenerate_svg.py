from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeRegenerateSvgContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.response = (
            ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
        ).read_text()

    def test_native_regenerate_svg_has_observed_root_contract(self) -> None:
        self.assertIn('viewBox="-2 -2 28 28"', self.response)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', self.response)
        self.assertIn('class="size-4"', self.response)
        self.assertIn('aria-hidden="true"', self.response)

    def test_native_regenerate_paths_keep_rendering_state_on_paths(self) -> None:
        for path in (
            "M21.1679 8C19.6247 4.46819 16.1006 2 11.9999 2C6.81459 2 2.55104 5.94668 2.04932 11",
            "M17 8H21.4C21.7314 8 22 7.73137 22 7.4V3",
            "M2.88146 16C4.42458 19.5318 7.94874 22 12.0494 22C17.2347 22 21.4983 18.0533 22 13",
            "M7.04932 16H2.64932C2.31795 16 2.04932 16.2686 2.04932 16.6V21",
        ):
            self.assertIn(f'd="{path}"', self.response)
        self.assertGreaterEqual(
            self.response.count(
                'stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"'
            ),
            4,
        )

    def test_legacy_regenerate_branch_remains_separate(self) -> None:
        self.assertIn("{#if v0113Compatibility}", self.response)
        self.assertIn("regenerate-response-button", self.response)


if __name__ == "__main__":
    unittest.main()
