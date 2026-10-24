#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Source-level regression checks for the passive citations HTML preview."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/lib/components/chat/Messages/CitationsModal.svelte"
MODAL = ROOT / "src/lib/components/common/Modal.svelte"
FOCUS_HELPER = ROOT / "src/lib/utils/modalFocus.js"


class CitationsModalSandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_html_preview_has_empty_sandbox_and_original_srcdoc(self):
        iframe = re.search(r"<iframe\b(?P<attributes>.*?)></iframe>", self.source, re.DOTALL)
        self.assertIsNotNone(iframe)
        attributes = iframe.group("attributes")
        self.assertRegex(attributes, r'\bsandbox=""')
        self.assertIn("srcdoc={document.document}", attributes)
        self.assertNotRegex(attributes, r"allow-(?:scripts|forms|same-origin)")

    def test_preview_notice_names_limited_interaction(self):
        self.assertIn("Static preview: scripts and forms are disabled.", self.source)
        self.assertRegex(
            self.source,
            r'<p class="text-sm text-gray-700 dark:text-gray-200 mb-1">',
        )

    def test_close_button_has_accessible_name_without_changing_action(self):
        button = re.search(r"<button\b(?P<attributes>.*?)on:click=\{\(\) => \{\s*show = false;", self.source, re.DOTALL)
        self.assertIsNotNone(button)
        self.assertIn('aria-label="Close citation"', button.group("attributes"))
        self.assertIn("show = false;", button.group(0))

    def test_notice_color_tokens_match_modal_palette(self):
        tailwind = (ROOT / "tailwind.config.js").read_text(encoding="utf-8")
        modal = (ROOT / "src/lib/components/common/Modal.svelte").read_text(encoding="utf-8")
        self.assertIn("700: 'var(--color-gray-700, #4e4e4e)'", tailwind)
        self.assertIn("200: 'var(--color-gray-200, #e3e3e3)'", tailwind)
        self.assertIn("bg-white dark:bg-gray-900", modal)

    def test_original_source_control_and_non_html_text_path_remain(self):
        self.assertIn("metadata?.file_id", self.source)
        self.assertIn("/content${document?.metadata?.page", self.source)
        self.assertIn("{decodeString(document?.metadata?.name ?? document.source.name)}", self.source)
        self.assertIn("{:else}", self.source)
        self.assertIn("<pre class=", self.source)
        self.assertIn("{document.document}", self.source)
        self.assertIn('title={$i18n.t(\'Content\')}', self.source)

    def test_html_canvas_is_stable_and_citation_modal_is_named_and_opt_in(self):
        iframe = re.search(r"<iframe\b(?P<attributes>.*?)></iframe>", self.source, re.DOTALL)
        self.assertIsNotNone(iframe)
        self.assertRegex(iframe.group("attributes"), r'\bbg-white\b')
        self.assertIn('style="color-scheme: light"', iframe.group("attributes"))
        self.assertIn('trapFocus={true} labelledBy="citation-modal-title"', self.source)
        self.assertRegex(self.source, r'id="citation-modal-title"')
        self.assertIn("data-modal-initial-focus", self.source)

    def test_focus_support_is_opt_in_and_restores_the_modal_boundary(self):
        modal = MODAL.read_text(encoding="utf-8")
        helper = FOCUS_HELPER.read_text(encoding="utf-8")
        self.assertIn("export let trapFocus = false", modal)
        self.assertIn("role={trapFocus ? 'dialog' : undefined}", modal)
        self.assertIn("aria-modal={trapFocus ? 'true' : undefined}", modal)
        self.assertIn("activateModalFocus(modalElement)", modal)
        self.assertIn("releaseModalFocus()", modal)
        for behavior in ("setAttribute('inert'", "removeAttribute('inert'", "event.key !== 'Tab'", "previousFocus.focus()"):
            self.assertIn(behavior, helper)


if __name__ == "__main__":
    unittest.main(verbosity=2)
