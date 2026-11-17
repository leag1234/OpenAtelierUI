# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured native response Info action contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text()


class CurrentInfoActionContractTests(unittest.TestCase):
    def test_native_info_action_keeps_identity_and_usage_relationship(self) -> None:
        self.assertIn("aria-label={!v0113Compatibility ? $i18n.t('Info') : undefined}", RESPONSE)
        self.assertIn("aria-describedby={!v0113Compatibility ? `usage-${message.id}` : undefined}", RESPONSE)
        self.assertIn('id="info-{message.id}"', RESPONSE)

    def test_native_info_action_uses_the_measured_visible_control_classes(self) -> None:
        self.assertIn(
            "'visible inline-flex size-7 shrink-0 items-center justify-center p-1.5 dark:hover:text-gray-400 hover:text-gray-600'",
            RESPONSE,
        )

    def test_native_info_icon_uses_three_observed_decorative_paths(self) -> None:
        self.assertIn('<svg class="size-4" aria-hidden="true" stroke-width="1.5"', RESPONSE)
        for path in (
            "M12 11.5V16.5",
            "M12 7.51L12.01 7.49889",
            "M12 22C17.5228 22 22 17.5228 22 12",
        ):
            self.assertIn(path, RESPONSE)

    def test_v0113_info_icon_and_hover_contract_remain_separate(self) -> None:
        self.assertIn("'hover-reveal p-1.5 dark:hover:text-white hover:text-black'", RESPONSE)
        self.assertIn('class="w-4 h-4"', RESPONSE)
        self.assertIn("M11.25 11.25l.041-.02", RESPONSE)


if __name__ == "__main__":
    unittest.main()
