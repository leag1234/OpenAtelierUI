#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Contracts for the ordinary French and keyboard-accessible home journey."""

from pathlib import Path
import ast
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = ROOT / "src/lib/components/chat/Placeholder.svelte"
CHAT_PLACEHOLDER = ROOT / "src/lib/components/chat/ChatPlaceholder.svelte"
SUGGESTIONS = ROOT / "src/lib/components/chat/Suggestions.svelte"
FRENCH = ROOT / "src/lib/i18n/locales/fr-FR/translation.json"
CONFIG = ROOT / "backend/open_webui/config.py"


class FrenchAccessibilityJourneyTests(unittest.TestCase):
    def test_model_avatar_buttons_have_localized_names(self):
        for path in (PLACEHOLDER, CHAT_PLACEHOLDER):
            source = path.read_text(encoding="utf-8")
            self.assertIn("aria-label={`${$i18n.t('Select a model')}", source)
            self.assertIn('alt=""', source)
            self.assertIn('aria-hidden="true"', source)

    def test_default_suggestions_render_and_submit_the_active_locale(self):
        source = SUGGESTIONS.read_text(encoding="utf-8")
        self.assertIn("$i18n.exists(value) ? $i18n.t(value) : value", source)
        self.assertIn("dispatch('select', localized(prompt.content))", source)
        self.assertIn("localized(prompt.title[0])", source)
        self.assertIn("localized(prompt.title[1])", source)

    def test_all_default_french_suggestion_strings_are_complete(self):
        french = json.loads(FRENCH.read_text(encoding="utf-8"))
        tree = ast.parse(CONFIG.read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG" for target in node.targets)
        )
        default_config = ast.literal_eval(assignment.value)
        prompts = default_config["ui"]["prompt_suggestions"]
        required = {
            value
            for prompt in prompts
            for value in [*prompt.get("title", []), prompt["content"]]
        }
        self.assertEqual(required - french.keys(), set())
        self.assertTrue(all(french[key] and french[key] != key for key in required))


if __name__ == "__main__":
    unittest.main(verbosity=2)
