#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Current-reference response typography must be active and profile-scoped."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "src/app.css").read_text(encoding="utf-8")


class CurrentResponsePresentationContractTests(unittest.TestCase):
    def test_native_typography_is_anchored_to_the_rendered_chat_shell(self) -> None:
        self.assertIn(
            "#chat-container[role='presentation'] .markdown-prose {", APP_CSS
        )
        self.assertNotIn("[data-current-compatibility] .markdown-prose", APP_CSS)

    def test_observed_heading_and_paragraph_metrics_are_profile_scoped(self) -> None:
        self.assertIn(
            "#chat-container[role='presentation'] .markdown-prose h1 {", APP_CSS
        )
        self.assertIn("font-size: 20px !important;", APP_CSS)
        self.assertIn(
            "#chat-container[role='presentation'] .markdown-prose p {", APP_CSS
        )
        self.assertIn("font-size: 14.5px !important;", APP_CSS)

    def test_rich_content_metrics_share_the_same_native_anchor(self) -> None:
        for selector in ("blockquote", "code.codespan", "table"):
            with self.subTest(selector=selector):
                self.assertIn(
                    f"#chat-container[role='presentation'] .markdown-prose {selector}",
                    APP_CSS,
                )


if __name__ == "__main__":
    unittest.main()
