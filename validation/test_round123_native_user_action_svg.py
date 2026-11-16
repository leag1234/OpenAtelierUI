from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
USER_MESSAGE = ROOT / "src/lib/components/chat/Messages/UserMessage.svelte"


class NativeUserActionSvgContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = USER_MESSAGE.read_text(encoding="utf-8")

    def test_native_edit_and_copy_icons_are_decorative(self) -> None:
        self.assertGreaterEqual(
            self.source.count("aria-hidden={!v0113Compatibility ? 'true' : undefined}"),
            2,
        )

    def test_native_edit_and_copy_strokes_are_explicit_on_paths(self) -> None:
        self.assertGreaterEqual(
            self.source.count("stroke={v0113Compatibility ? 'currentColor' : undefined}"),
            2,
        )
        self.assertGreaterEqual(
            self.source.count("stroke={!v0113Compatibility ? 'currentColor' : undefined}"),
            2,
        )
        self.assertIn('<path\n', self.source)
        self.assertIn('stroke="currentColor"', self.source)

    def test_legacy_profile_retains_inherited_svg_stroke(self) -> None:
        self.assertIn(
            "stroke={v0113Compatibility ? 'currentColor' : undefined}",
            self.source,
        )
        self.assertIn(
            "stroke={!v0113Compatibility ? 'currentColor' : undefined}",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
