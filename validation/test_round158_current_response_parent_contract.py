# SPDX-License-Identifier: BSD-3-Clause
"""Regression checks for the measured native response parent contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RESPONSE = (
    ROOT / "src/lib/components/chat/Messages/ResponseMessage.svelte"
).read_text()


class CurrentResponseParentContractTests(unittest.TestCase):
    def test_native_response_root_uses_the_measured_stable_class_contract(self) -> None:
        self.assertIn(
            "v0113Compatibility ? `group flex w-full message-${message.id}` : 'flex w-full'",
            RESPONSE,
        )
        self.assertNotIn(
            ": `flex w-full message-${message.id}`",
            RESPONSE,
        )

    def test_v0113_response_root_keeps_its_legacy_message_class(self) -> None:
        self.assertIn(
            "`group flex w-full message-${message.id}`",
            RESPONSE,
        )

    def test_response_identity_and_direct_children_remain_stable(self) -> None:
        root = RESPONSE.index('id="message-{message.id}"')
        avatar = RESPONSE.index("'shrink-0 ltr:mr-2 rtl:ml-2 hidden @lg:flex mt-0.5'")
        body = RESPONSE.index('<div class="flex-auto w-0 pl-1 relative">')
        self.assertLess(root, avatar)
        self.assertLess(avatar, body)


if __name__ == "__main__":
    unittest.main()
