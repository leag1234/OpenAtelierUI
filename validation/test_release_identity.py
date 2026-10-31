#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Keep release status and translation navigation project-specific."""

from __future__ import annotations

import unittest
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
CHANGELOG_MODAL = BASE / "src/lib/components/ChangelogModal.svelte"
GENERAL_SETTINGS = BASE / "src/lib/components/chat/Settings/General.svelte"


class ReleaseIdentityTests(unittest.TestCase):
    def test_modal_uses_truthful_development_notes_without_legacy_changelog_fetch(self):
        source = CHANGELOG_MODAL.read_text(encoding="utf-8")
        self.assertIn("Development status for {{WEBUI_NAME}}", source)
        self.assertIn("Development build", source)
        self.assertIn("No published release number", source)
        self.assertIn("still being validated", source)
        self.assertNotIn("getChangelog", source)
        self.assertNotIn("WEBUI_VERSION", source)
        self.assertNotIn("changelog[version]", source)
        self.assertNotIn("v{version}", source)

    def test_translation_link_points_to_this_projects_locale_sources(self):
        source = GENERAL_SETTINGS.read_text(encoding="utf-8")
        self.assertIn("https://github.com/leag1234/OpenAtelierUI/tree/main/src/lib/i18n/locales", source)
        self.assertIn("Contribute a translation to OpenAtelierUI", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
