from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeSmallSvgContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bolt = (ROOT / "src/lib/components/icons/Bolt.svelte").read_text()
        cls.chevrons = (ROOT / "src/lib/components/icons/ChevronUpDown.svelte").read_text()
        cls.clipboard = (ROOT / "src/lib/components/icons/Clipboard.svelte").read_text()
        cls.suggestions = (ROOT / "src/lib/components/chat/Suggestions.svelte").read_text()
        cls.code = (ROOT / "src/lib/components/chat/Messages/CodeBlock.svelte").read_text()
        cls.markdown = (
            ROOT / "src/lib/components/chat/Messages/Markdown/MarkdownTokens.svelte"
        ).read_text()

    def test_native_suggestion_and_code_icons_are_decorative(self) -> None:
        for source in (self.bolt, self.chevrons):
            self.assertIn("export let ariaHidden = false;", source)
            self.assertIn("aria-hidden={ariaHidden ? 'true' : undefined}", source)
        self.assertIn("<Bolt ariaHidden={!v0113Compatibility} />", self.suggestions)
        self.assertIn(
            '<ChevronUpDown className="size-3" ariaHidden={currentCompatibility} />',
            self.code,
        )

    def test_native_table_copy_keeps_legacy_button_type_separate(self) -> None:
        self.assertIn("export let ariaHidden = false;", self.clipboard)
        self.assertIn("type={currentCompatibility ? undefined : 'button'}", self.markdown)
        self.assertIn("ariaHidden={currentCompatibility}", self.markdown)

    def test_native_table_export_restores_accessible_name_and_path_state(self) -> None:
        self.assertIn(
            "aria-label={currentCompatibility ? $i18n.t('Export to CSV') : undefined}",
            self.markdown,
        )
        self.assertIn('aria-hidden="true"', self.markdown)
        self.assertIn(
            '<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v11" />',
            self.markdown,
        )


if __name__ == "__main__":
    unittest.main()
