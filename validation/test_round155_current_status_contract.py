# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured current status and interruption family."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESPONSE = (ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte").read_text()
CURRENT_STATUS = (
    ROOT / "src/lib/components/chat/Messages/ResponseStatusCurrent.svelte"
).read_text()


class CurrentStatusContractTests(unittest.TestCase):
    def test_current_and_v0113_status_paths_are_isolated(self) -> None:
        self.assertIn("{#if v0113Compatibility}", RESPONSE)
        self.assertIn("<ResponseStatusCurrent {status} />", RESPONSE)
        self.assertIn('<Spinner className="size-4" />', RESPONSE)
        self.assertNotIn(
            "!(!v0113Compatibility && status?.action === 'response_stopped')",
            RESPONSE,
        )

    def test_current_status_exports_the_measured_structure(self) -> None:
        self.assertIn('class="text-[0.90625rem] flex flex-col w-full"', CURRENT_STATUS)
        self.assertIn('class="relative w-full"', CURRENT_STATUS)
        self.assertIn('class="absolute inset-0"', CURRENT_STATUS)
        self.assertIn('aria-expanded="false"', CURRENT_STATUS)
        self.assertIn(
            'class="relative pointer-events-none flex items-start gap-2"',
            CURRENT_STATUS,
        )
        self.assertIn(
            'class="status-description flex items-center gap-2 py-0.5 w-full text-left"',
            CURRENT_STATUS,
        )

    def test_current_status_has_no_legacy_spinner_or_skeleton(self) -> None:
        self.assertNotIn("Spinner", CURRENT_STATUS)
        self.assertNotIn("Skeleton", CURRENT_STATUS)


if __name__ == "__main__":
    unittest.main()
