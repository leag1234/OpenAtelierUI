# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured native response timestamp contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text()
APP_CSS = (ROOT / "src/app.css").read_text()


class CurrentResponseTimestampContractTests(unittest.TestCase):
    def test_native_timestamp_retains_the_reference_dom_contract(self) -> None:
        self.assertIn(
            "message.timestamp && message.done && !v0113Compatibility",
            RESPONSE,
        )
        self.assertIn(
            '<div class="flex self-center">\n\t\t\t\t\t\t\t\t\t<time',
            RESPONSE,
        )
        self.assertIn(
            'class="hover-reveal ml-1 shrink-0 whitespace-nowrap '
            'text-[0.6875rem] tabular-nums text-gray-400 '
            'dark:text-gray-600 select-none"',
            RESPONSE,
        )

    def test_native_phone_timestamp_is_structural_but_not_visible(self) -> None:
        self.assertIn("@media (max-width: 1023px)", APP_CSS)
        self.assertIn(
            "#chat-container .buttons > .flex.self-center > time.hover-reveal",
            APP_CSS,
        )
        self.assertIn("display: none", APP_CSS)

    def test_v0113_timestamp_branch_remains_separate(self) -> None:
        self.assertIn("message.timestamp && v0113Compatibility", RESPONSE)
        self.assertIn("message-timestamp hover-reveal", RESPONSE)


if __name__ == "__main__":
    unittest.main()
