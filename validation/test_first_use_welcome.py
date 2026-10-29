#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Check first-use welcome action and auth focus isolation in own UI source."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
WELCOME = BASE / "src/lib/components/OnBoarding.svelte"
AUTH = BASE / "src/routes/auth/+page.svelte"


class FirstUseWelcomeTests(unittest.TestCase):
    def test_welcome_is_branded_single_action_with_visible_accessible_name(self):
        source = WELCOME.read_text(encoding="utf-8")
        buttons = re.findall(r"<button\b[\s\S]*?</button>", source)
        self.assertEqual(len(buttons), 1)
        self.assertIn("Continue to account setup", buttons[0])
        self.assertIn("Welcome to {{WEBUI_NAME}}", source)
        self.assertIn("conversations, files, and models", source)
        self.assertIn("aria-labelledby=\"first-use-heading\"", source)
        self.assertNotIn("Marquee", source)
        self.assertNotIn("SlideShow", source)
        self.assertNotIn("Explore the cosmos", source)

    def test_auth_controls_are_inert_until_activation_then_first_field_is_focused(self):
        source = AUTH.read_text(encoding="utf-8")
        self.assertIn("aria-hidden={onboarding}", source)
        self.assertIn("inert={onboarding}", source)
        self.assertIn("return tick().then(() =>", source)
        self.assertIn("document.getElementById(firstFieldId)?.focus()", source)
        self.assertIn("for=\"sign-up-name\"", source)
        self.assertIn("id=\"sign-up-name\"", source)
        self.assertIn("for=\"sign-in-email\"", source)
        self.assertIn("for=\"sign-in-password\"", source)
        self.assertIn("for=\"ldap-username\"", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
