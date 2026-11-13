#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Residual visual-contract checks reported by the Atlas Round 17 replay."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILE_ITEM = ROOT / "src/lib/components/common/FileItem.svelte"
MESSAGE_INPUT = ROOT / "src/lib/components/chat/MessageInput.svelte"
RESPONSE = ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
USER_MESSAGE = ROOT / "src/lib/components/chat/Messages/UserMessage.svelte"
PLACEHOLDER = ROOT / "src/lib/components/chat/Placeholder.svelte"


class Round17V0113VisualProfileTests(unittest.TestCase):
    def test_composer_uses_the_legacy_small_one_line_file_chip_only_in_profile(self):
        message_input = MESSAGE_INPUT.read_text(encoding="utf-8")
        file_item = FILE_ITEM.read_text(encoding="utf-8")
        self.assertIn(
            "$: v0113Compatibility = $config?.compatibility_profile !== 'native';",
            message_input,
        )
        self.assertIn("small={v0113Compatibility}", message_input)
        self.assertIn("legacyCompact={v0113Compatibility}", message_input)
        self.assertIn("export let legacyCompact = false;", file_item)
        self.assertIn(
            'className="flex min-w-0 flex-1 overflow-hidden"', file_item
        )
        self.assertIn(
            'class="min-w-0 flex-1 truncate pr-1 font-normal"', file_item
        )
        self.assertIn("max-w-[35%] shrink-0 truncate", file_item)

    def test_home_identity_stays_in_one_row_in_the_legacy_profile(self):
        source = PLACEHOLDER.read_text(encoding="utf-8")
        self.assertIn(
            "$: v0113Compatibility = $config?.compatibility_profile !== 'native';",
            source,
        )
        self.assertIn(
            '<div class="text-2xl line-clamp-1 flex items-center whitespace-nowrap"',
            source,
        )
        self.assertIn('class="line-clamp-1 whitespace-nowrap"', source)

    def test_phone_assistant_contract_removes_extra_gutter_and_timestamp(self):
        source = RESPONSE.read_text(encoding="utf-8")
        self.assertIn("'hidden shrink-0 ltr:mr-2 rtl:ml-2 @lg:flex mt-0.5'", source)
        self.assertIn("'size-7 assistant-message-profile-image'", source)
        self.assertIn('class="flex-auto w-0 pl-1 {v0113Compatibility', source)
        self.assertIn("`${WEBUI_API_BASE_URL}/models/model/profile/image", source)
        self.assertIn("'hidden @lg:block ml-1 shrink-0 whitespace-nowrap", source)

    def test_legacy_user_bubble_is_compact_and_mobile_timestamp_has_no_row(self):
        source = USER_MESSAGE.read_text(encoding="utf-8")
        self.assertIn(
            "$: v0113Compatibility = $config?.compatibility_profile !== 'native';",
            source,
        )
        self.assertIn("{#if message.content && v0113Compatibility}", source)
        self.assertIn('class="whitespace-pre-wrap text-[0.9375rem]"', source)
        self.assertIn("{:else if message.content}", source)
        self.assertIn("'hidden @lg:block mr-1 shrink-0 whitespace-nowrap", source)

    def test_native_branches_remain_explicit(self):
        response = RESPONSE.read_text(encoding="utf-8")
        user = USER_MESSAGE.read_text(encoding="utf-8")
        file_item = FILE_ITEM.read_text(encoding="utf-8")
        self.assertIn("'hidden shrink-0 ltr:mr-3 rtl:ml-3 lg:flex'", response)
        self.assertIn("'block mt-1 text-xs text-gray-500'", response)
        self.assertIn("'block mt-1 text-xs text-gray-500'", user)
        self.assertIn("{:else if !small}", file_item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
