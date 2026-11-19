# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured current attachment attribute family."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CURRENT_FILE = (ROOT / "src/lib/components/common/FileItemCurrent.svelte").read_text()
LEGACY_FILE = (ROOT / "src/lib/components/common/FileItem.svelte").read_text()
MESSAGE_INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text()


class CurrentAttachmentAttributeTests(unittest.TestCase):
    def test_current_chip_omits_measured_extra_attributes(self) -> None:
        self.assertNotIn("title=", CURRENT_FILE)
        self.assertIn(
            '<div class="shrink-0 text-gray-500 dark:text-gray-400">',
            CURRENT_FILE,
        )
        self.assertIn('<div class="flex">', CURRENT_FILE)
        self.assertNotIn('<div class="flex justify-center text-center">', CURRENT_FILE)
        self.assertEqual(CURRENT_FILE.count('aria-hidden="true"'), 2)

    def test_visible_name_and_remove_control_keep_accessible_text(self) -> None:
        self.assertIn("{decodeString(name)}", CURRENT_FILE)
        self.assertIn('aria-label="Remove File"', CURRENT_FILE)

    def test_current_and_legacy_components_remain_profile_isolated(self) -> None:
        self.assertIn(
            "this={v0113Compatibility ? FileItem : FileItemCurrent}",
            MESSAGE_INPUT,
        )
        self.assertIn('aria-hidden="true"', LEGACY_FILE)


if __name__ == "__main__":
    unittest.main()
