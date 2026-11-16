from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESPONSE_MESSAGE = ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"


class NativeSvgStateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = RESPONSE_MESSAGE.read_text(encoding="utf-8")

    def test_profile_moves_decorative_action_stroke_to_paths(self) -> None:
        self.assertGreaterEqual(
            self.source.count("aria-hidden={!v0113Compatibility ? 'true' : undefined}"),
            7,
        )
        self.assertGreaterEqual(
            self.source.count("stroke={v0113Compatibility ? 'currentColor' : undefined}"),
            7,
        )
        self.assertGreaterEqual(
            self.source.count("stroke={!v0113Compatibility ? 'currentColor' : undefined}"),
            7,
        )

    def test_native_only_action_icons_keep_explicit_path_state(self) -> None:
        self.assertIn(
            'viewBox="-2 -2 28 28"',
            self.source,
        )
        self.assertIn(
            '<path d="M21.1679 8C19.6247 4.46819 16.1006 2 11.9999 2C6.81459 2 2.55104 5.94668 2.04932 11" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />',
            self.source,
        )
        self.assertIn(
            '<path d="M7 21V10" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />',
            self.source,
        )
        self.assertIn(
            '<path d="M17 3v11" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" />',
            self.source,
        )

    def test_legacy_profile_retains_inherited_svg_stroke(self) -> None:
        self.assertIn("$: v0113Compatibility = $config?.compatibility_profile !== 'native';", self.source)
        self.assertIn(
            "stroke-linecap={v0113Compatibility ? 'round' : undefined}",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
