#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Native reference state attributes stay useful without duplicate labelling."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLTIP = (ROOT / "src/lib/components/common/Tooltip.svelte").read_text(
    encoding="utf-8"
)
USER_MESSAGE = (
    ROOT / "src/lib/components/chat/Messages/UserMessage.svelte"
).read_text(encoding="utf-8")
RESPONSE_MESSAGE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text(encoding="utf-8")


class NativeStateContractTests(unittest.TestCase):
    def test_native_tooltip_wrapper_omits_duplicate_accessible_name(self) -> None:
        self.assertIn("import { config } from '$lib/stores';", TOOLTIP)
        self.assertIn(
            "nativeCompatibility = $config?.compatibility_profile === 'native'",
            TOOLTIP,
        )
        self.assertIn(
            "aria-label={nativeCompatibility ? undefined : DOMPurify.sanitize(content)}",
            TOOLTIP,
        )

    def test_v0113_tooltip_wrapper_label_path_is_retained(self) -> None:
        self.assertIn("DOMPurify.sanitize(content)", TOOLTIP)
        self.assertNotIn("aria-label={undefined}", TOOLTIP)

    def test_message_direction_has_a_stable_default(self) -> None:
        expected = "dir={$settings?.chatDirection ?? 'auto'}"
        self.assertIn(expected, USER_MESSAGE)
        self.assertIn(expected, RESPONSE_MESSAGE)


if __name__ == "__main__":
    unittest.main()
