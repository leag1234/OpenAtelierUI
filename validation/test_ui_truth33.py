# SPDX-License-Identifier: BSD-3-Clause
"""Focused source-contract checks for the truthful first-use notice and editor label."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class UITruthTests(unittest.TestCase):
    def test_first_use_notice_describes_configured_processing_without_guarantees(self):
        source = (ROOT / "src/routes/auth/+page.svelte").read_text(encoding="utf-8")
        self.assertIn("Requests may be sent to external providers configured by the server administrator", source)
        self.assertIn("check provider settings before sharing sensitive information", source)
        self.assertNotIn("does not make any external connections", source)
        self.assertNotIn("data stays securely on your locally hosted server", source)

    def test_locale_bound_composer_label_updates_editor_dom_in_place(self):
        message_input = (ROOT / "src/lib/components/chat/MessageInput.svelte").read_text(encoding="utf-8")
        rich_text = (ROOT / "src/lib/components/common/RichTextInput.svelte").read_text(encoding="utf-8")
        self.assertIn("ariaLabel={$i18n.t('Send a Message')}", message_input)
        reactive = rich_text.split("$: if (editor) {", 1)[1].split("\n\t}", 1)[0]
        self.assertIn("editor.view.dom.setAttribute('aria-label', ariaLabel)", reactive)
        self.assertIn("editor.view.dom.removeAttribute('aria-label')", reactive)
        self.assertNotIn("new Editor", reactive)
        self.assertNotIn("setContent", reactive)


if __name__ == "__main__":
    unittest.main()
