#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Static contracts for the independently regenerated current home shell."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHAT = (ROOT / "src/lib/components/chat/Chat.svelte").read_text(encoding="utf-8")
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(encoding="utf-8")
MODEL = (ROOT / "src/lib/components/chat/ModelSelector.svelte").read_text(encoding="utf-8")
SELECTOR = (ROOT / "src/lib/components/chat/ModelSelector/Selector.svelte").read_text(
    encoding="utf-8"
)
PLACEHOLDER = (ROOT / "src/lib/components/chat/Placeholder.svelte").read_text(
    encoding="utf-8"
)
SUGGESTIONS = (ROOT / "src/lib/components/chat/Suggestions.svelte").read_text(
    encoding="utf-8"
)


class CurrentPageShellTests(unittest.TestCase):
    def test_current_shell_keeps_min_width_and_scroll_hierarchy(self) -> None:
        self.assertIn("nativeCompatibility ? 'min-w-0'", CHAT)
        self.assertIn("'w-full h-full flex flex-col'", CHAT)
        self.assertIn("'relative w-full h-full min-h-0 flex'", CHAT)
        self.assertIn("@container overflow-auto", CHAT)
        self.assertIn('<div class="sr-only"></div>', CHAT)

    def test_current_model_selector_uses_compact_observed_hierarchy(self) -> None:
        self.assertIn("flex min-w-0 max-w-full flex-col items-start", MODEL)
        self.assertIn("min-w-0 max-w-full overflow-hidden", MODEL)
        self.assertIn("nativeCompact={true}", MODEL)
        self.assertIn("focus-ring relative w-full", SELECTOR)
        self.assertIn("text-[0.8125rem] font-normal", SELECTOR)
        self.assertIn("span class=\"min-w-0 flex-1 truncate\"", SELECTOR)

    def test_current_home_width_and_suggestion_bound_are_explicit(self) -> None:
        self.assertIn("'max-w-[58rem]'", INPUT)
        self.assertIn("v0113Compatibility ? 'font-primary' : ''", PLACEHOLDER)
        self.assertIn("filteredPrompts.slice(0, 6)", SUGGESTIONS)

    def test_legacy_profile_branches_remain_present(self) -> None:
        self.assertIn("'max-w-6xl'", INPUT)
        self.assertIn("'relative w-full font-primary'", SELECTOR)
        self.assertIn("'flex flex-col w-full items-start'", MODEL)
        self.assertIn("filteredPrompts as prompt", SUGGESTIONS)


if __name__ == "__main__":
    unittest.main()
