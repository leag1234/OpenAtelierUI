#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source contracts for the production follow-up localization and phone UI."""

from pathlib import Path
import json
import unittest


ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "src/lib/i18n/index.ts"
AUTH = ROOT / "src/routes/auth/+page.svelte"
CHAT = ROOT / "src/lib/components/chat/Chat.svelte"
COMPOSER = ROOT / "src/lib/components/chat/MessageInput.svelte"
FRENCH = ROOT / "src/lib/i18n/locales/fr-FR/translation.json"


class ProductionFollowupUiTests(unittest.TestCase):
    def test_login_waits_for_detected_locale_before_success_toast(self):
        i18n = I18N.read_text(encoding="utf-8")
        auth = AUTH.read_text(encoding="utf-8")
        french = json.loads(FRENCH.read_text(encoding="utf-8"))
        self.assertIn("export const waitForI18n = () => i18nReady;", i18n)
        wait = auth.index("await waitForI18n();")
        toast = auth.index("toast.success($i18n.t(`You're now logged in.`));")
        self.assertLess(wait, toast)
        self.assertEqual(french["You're now logged in."], "Vous êtes désormais connecté.")

    def test_phone_moves_model_controls_out_of_composer(self):
        chat = CHAT.read_text(encoding="utf-8")
        composer = COMPOSER.read_text(encoding="utf-8")
        self.assertIn("showModelSelector={$mobile}", chat)
        self.assertIn(
            'class="hidden sm:flex max-w-[13rem] @md:max-w-[18rem] min-w-0 items-center"',
            composer,
        )
        self.assertEqual(composer.count('id="input-menu-button"'), 1)
        self.assertEqual(
            composer.count("<ModelSelector bind:selectedModels showSetDefault={false} />"),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
