from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text()
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text()
PLACEHOLDER = (ROOT / "src/lib/components/chat/Placeholder.svelte").read_text()


class NativeParentContractTests(unittest.TestCase):
    def test_native_home_model_keeps_keyboard_semantics_and_observed_attributes(self) -> None:
        self.assertIn("aria-hidden={!v0113Compatibility ? 'false' : undefined}", PLACEHOLDER)
        self.assertIn("tabindex={!v0113Compatibility ? 0 : undefined}", PLACEHOLDER)
        self.assertIn("crossorigin={v0113Compatibility ? 'anonymous' : undefined}", PLACEHOLDER)
        self.assertIn("alt={v0113Compatibility ? '' : undefined}", PLACEHOLDER)

    def test_native_model_controls_use_observed_layout_neutral_parent(self) -> None:
        marker = "aria-label={$i18n.t('Model controls')}"
        start = INPUT.index(marker) - 500
        end = INPUT.index(marker) + 500
        region = INPUT[start:end]
        self.assertIn('<span style="display: contents; cursor: pointer;">', region)
        self.assertIn('<div class="flex">', region)

    def test_native_spacer_stays_inside_composer_form(self) -> None:
        self.assertIn('{#if !v0113Compatibility}<div class="mb-0.5"></div>{/if}', INPUT)
        self.assertNotIn('{#if !v0113Compatibility}<div class="mb-0.5"></div>{/if}', PLACEHOLDER)

    def test_native_pane_group_keeps_outer_shell_distinct(self) -> None:
        self.assertIn(
            "class={nativeCompatibility ? 'w-full h-full flex flex-col' : 'w-full h-full'}",
            CHAT,
        )

    def test_controls_remain_inside_the_main_pane(self) -> None:
        controls = CHAT.index("\n\t\t\t<ChatControls")
        pane_close = CHAT.index("\n\t\t\t</Pane>", controls)
        group_close = CHAT.index("\n\t\t</PaneGroup>", pane_close)
        self.assertLess(controls, pane_close)
        self.assertLess(pane_close, group_close)


if __name__ == "__main__":
    unittest.main()
