from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NativeCrossPageAttributeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.message_input = (
            ROOT / "src/lib/components/chat/MessageInput.svelte"
        ).read_text()
        cls.response = (
            ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
        ).read_text()

    def test_model_controls_expose_dialog_state_without_changing_click_behavior(self) -> None:
        marker = "aria-label={$i18n.t('Model controls')}"
        start = self.message_input.index(marker) - 240
        end = self.message_input.index("</button>", start)
        control = self.message_input[start:end]
        self.assertIn('type="button"', control)
        self.assertIn('aria-haspopup="dialog"', control)
        self.assertIn("aria-expanded={$showControls ? 'true' : 'false'}", control)
        self.assertIn("on:click={() => showControls.set(!$showControls)}", control)

    def test_native_edit_glyph_keeps_round_path_endings(self) -> None:
        marker = 'd="M16.862 4.487l1.687-1.688'
        start = self.response.rfind("<path", 0, self.response.index(marker))
        end = self.response.index("/>", start)
        path = self.response[start:end]
        self.assertIn('stroke-linecap="round"', path)
        self.assertIn('stroke-linejoin="round"', path)
        self.assertIn("stroke={!v0113Compatibility ? 'currentColor' : undefined}", path)


if __name__ == "__main__":
    unittest.main()
