#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Static contracts for the independently regenerated current attachment state."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INPUT = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(encoding="utf-8")
FILE_ITEM = (ROOT / "src/lib/components/common/FileItemCurrent.svelte").read_text(
    encoding="utf-8"
)


class CurrentAttachmentTests(unittest.TestCase):
    def test_current_attachment_wrapper_uses_compact_observed_flow(self) -> None:
        self.assertIn("'mx-2 mt-2 pb-1 flex items-center flex-wrap gap-1.5'", INPUT)
        self.assertIn("this={v0113Compatibility ? FileItem : FileItemCurrent}", INPUT)
        self.assertIn("dir={$settings?.chatDirection ?? 'auto'}", INPUT)

    def test_current_file_chip_is_compact_and_dismissible(self) -> None:
        self.assertIn('class="relative group w-60"', FILE_ITEM)
        self.assertIn("border-gray-50/30 dark:border-gray-800/30 h-8", FILE_ITEM)
        self.assertIn('class="flex min-w-0 flex-1 overflow-hidden"', FILE_ITEM)
        self.assertIn('class="absolute -top-[3px] -right-[3px] flex"', FILE_ITEM)
        self.assertIn("on:click|stopPropagation={() => dispatch('dismiss')}", FILE_ITEM)

    def test_current_editor_and_send_button_keep_compact_spacing(self) -> None:
        self.assertIn("max-h-96 overflow-auto'", INPUT)
        self.assertNotIn("max-h-96 overflow-auto pt-2'", INPUT)
        self.assertIn("v0113Compatibility\n", INPUT)
        self.assertIn(": 'p-[0.3125rem]'} self-center", INPUT)

    def test_legacy_attachment_component_remains_available(self) -> None:
        self.assertIn("import FileItem from '../common/FileItem.svelte';", INPUT)
        self.assertIn("legacyCompact={v0113Compatibility}", INPUT)


if __name__ == "__main__":
    unittest.main()
