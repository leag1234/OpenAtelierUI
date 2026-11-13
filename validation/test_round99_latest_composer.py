# SPDX-License-Identifier: BSD-3-Clause
"""Static contracts for the independently regenerated native composer slice."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MESSAGE_INPUT = ROOT / "src/lib/components/chat/MessageInput.svelte"
SUGGESTIONS = ROOT / "src/lib/components/chat/Suggestions.svelte"


class LatestComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.input = MESSAGE_INPUT.read_text(encoding="utf-8")
        cls.suggestions = SUGGESTIONS.read_text(encoding="utf-8")

    def test_native_composer_uses_observed_width_and_surface(self) -> None:
        self.assertIn("'max-w-[58rem]'} px-2 mx-auto inset-x-0", self.input)
        self.assertIn("'w-full flex flex-col gap-1.5'", self.input)
        self.assertIn("data-composer-id={!v0113Compatibility ? 'chat-input' : undefined}", self.input)

    def test_native_composer_keeps_direct_controls(self) -> None:
        self.assertIn("<ModelSelector bind:selectedModels showSetDefault={false} />", self.input)
        self.assertIn("aria-label={$i18n.t('Model controls')}", self.input)
        self.assertIn("id=\"input-menu-button\"", self.input)
        self.assertIn("id=\"voice-input-button\"", self.input)

    def test_native_suggestions_use_list_semantics(self) -> None:
        self.assertIn('role="list" aria-labelledby="suggestions-heading"', self.suggestions)
        self.assertIn('role="listitem" class="contents"', self.suggestions)
        self.assertIn('<h3 id="suggestions-heading">', self.suggestions)

    def test_v0113_composer_contract_remains_present(self) -> None:
        self.assertIn("'max-w-[640px]'} px-2.5 mx-auto inset-x-0", self.input)
        self.assertIn("'w-full flex flex-col items-stretch gap-1.5'", self.input)
        self.assertIn("`h-40 overflow-auto scrollbar-none", self.suggestions)


if __name__ == "__main__":
    unittest.main()
