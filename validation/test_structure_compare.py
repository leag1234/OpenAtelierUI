# SPDX-License-Identifier: BSD-3-Clause

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from validation.structure_compare import (
    ElementSignature,
    compare_html,
    compare_signatures,
    normalize_classes,
    signatures_from_html,
)


class StructureCompareTests(unittest.TestCase):
    def test_svelte_hashes_are_removed_but_class_order_is_preserved(self) -> None:
        self.assertEqual(
            normalize_classes("flex svelte-ab12 compact svelte-X_Y"),
            ("flex", "compact"),
        )

    def test_html_parser_returns_tag_and_complete_normalized_class_list(self) -> None:
        signatures = signatures_from_html(
            '<main class="flex svelte-abc"><button class="rounded primary">Go</button></main>'
        )
        self.assertEqual(
            signatures,
            (
                ElementSignature("main", ("flex",)),
                ElementSignature("button", ("rounded", "primary")),
            ),
        )

    def test_declared_audio_subtree_can_be_excluded(self) -> None:
        html = (
            '<main class="page"><div class="hidden-audio-visualizer">'
            '<canvas class="bars"></canvas></div><p class="answer">A</p></main>'
        )
        signatures = signatures_from_html(
            html, excluded_classes=("hidden-audio-visualizer",)
        )
        self.assertEqual(
            signatures,
            (
                ElementSignature("main", ("page",)),
                ElementSignature("p", ("answer",)),
            ),
        )

    def test_body_scope_ignores_generated_head_asset_graph(self) -> None:
        left = (
            '<html><head><link class="asset-a"><script class="chunk-a"></script></head>'
            '<body><main class="page"></main></body></html>'
        )
        right = (
            '<html><head><link class="asset-b"><link class="asset-c"></head>'
            '<body><main class="page"></main></body></html>'
        )
        result = compare_html(left, right, scope="body")
        self.assertTrue(result.passed)
        self.assertEqual(result.percentage, 100.0)
        self.assertEqual(result.left_count, 2)
        self.assertEqual(result.right_count, 2)

    def test_body_scope_keeps_every_body_ui_class_difference(self) -> None:
        result = compare_html(
            '<html><head></head><body class="theme"><main class="page"><p class="answer"></p></main></body></html>',
            '<html><head></head><body class="theme"><main class="page"><p class="reply"></p></main></body></html>',
            scope="body",
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.left_count, 3)
        self.assertEqual(result.right_count, 3)
        self.assertEqual(result.opcodes[0]["left_preview"], ["p.answer"])
        self.assertEqual(result.opcodes[0]["right_preview"], ["p.reply"])

    def test_identical_documents_pass_at_one_hundred_percent(self) -> None:
        html = '<main class="page"><p class="answer">A</p></main>'
        result = compare_html(html, html)
        self.assertTrue(result.passed)
        self.assertEqual(result.percentage, 100.0)
        self.assertEqual(result.opcodes, ())

    def test_changed_class_fails_and_reports_a_bounded_diff(self) -> None:
        result = compare_html(
            '<main class="page"><p class="answer">A</p></main>',
            '<main class="page"><p class="reply">A</p></main>',
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.left_count, 2)
        self.assertEqual(result.right_count, 2)
        self.assertEqual(len(result.opcodes), 1)
        self.assertEqual(result.opcodes[0]["operation"], "replace")
        self.assertEqual(result.opcodes[0]["left_preview"], ["p.answer"])
        self.assertEqual(result.opcodes[0]["right_preview"], ["p.reply"])

    def test_reference_floor_is_an_additional_gate(self) -> None:
        left = tuple(ElementSignature("div", (str(index),)) for index in range(100))
        right = left[:-1] + (ElementSignature("span", ("99",)),)
        measured = compare_signatures(left, right, threshold=90.0)
        self.assertGreaterEqual(measured.percentage, 90.0)
        result = compare_signatures(
            left, right, threshold=90.0, reference_floor=99.5
        )
        self.assertFalse(result.passed)

    def test_cli_returns_nonzero_and_writes_json_on_gate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.html"
            right = root / "right.html"
            output = root / "report.json"
            left.write_text('<main class="a"></main>', encoding="utf-8")
            right.write_text('<main class="b"></main>', encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("structure_compare.py")),
                    str(left),
                    str(right),
                    "--output",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(payload["passed"])
            self.assertEqual(payload["threshold"], 99.0)


if __name__ == "__main__":
    unittest.main()
