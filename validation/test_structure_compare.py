# SPDX-License-Identifier: BSD-3-Clause

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from validation.structure_compare import (
    ContractSignature,
    ElementSignature,
    compare_contract_html,
    compare_contract_signatures,
    compare_html,
    compare_signatures,
    contract_signatures_from_html,
    normalize_classes,
    normalize_contract_classes,
    normalize_identifier,
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

    def test_contract_normalizes_class_order_and_run_specific_uuid(self) -> None:
        self.assertEqual(
            normalize_contract_classes(
                "second svelte-build message-123e4567-e89b-12d3-a456-426614174000 first"
            ),
            ("first", "message-<id>", "second"),
        )
        self.assertEqual(
            normalize_identifier("message-123e4567-e89b-12d3-a456-426614174000"),
            "message-<id>",
        )

    def test_contract_records_id_attributes_and_immediate_parent(self) -> None:
        signatures = contract_signatures_from_html(
            '<html><body><main class="page"><button id="send" class="primary action" '
            'aria-label="Send" data-state="open" style="color:red">Go</button></main></body></html>'
        )
        self.assertEqual(
            signatures[-1],
            ContractSignature(
                tag="button",
                classes=("action", "primary"),
                identifier="send",
                attribute_names=("aria-label", "data-state", "id"),
                parent=("main", ("page",)),
            ),
        )

    def test_contract_fails_when_only_accessibility_attribute_differs(self) -> None:
        left = (
            '<html><body><main class="page"><div class="slot">'
            '<button class="action" aria-label="Send">Go</button></div></main></body></html>'
        )
        right = (
            '<html><body><main class="page"><div class="slot">'
            '<button class="action" title="Send">Go</button></div></main></body></html>'
        )
        self.assertTrue(compare_html(left, right, scope="body").passed)
        contract = compare_contract_html(left, right, scope="body")
        self.assertFalse(contract.passed)
        self.assertLess(contract.percentage, 99.0)

    def test_contract_fails_when_immediate_parent_differs(self) -> None:
        left = (
            '<html><body><main class="page"><div class="slot">'
            '<button class="action">Go</button></div><section class="other"></section>'
            '</main></body></html>'
        )
        right = (
            '<html><body><main class="page"><div class="slot"></div>'
            '<section class="other"><button class="action">Go</button></section>'
            '</main></body></html>'
        )
        contract = compare_contract_html(left, right, scope="body")
        self.assertFalse(contract.passed)
        self.assertLess(contract.percentage, 99.0)

    def test_contract_is_a_multiset_and_ignores_sibling_order(self) -> None:
        first = ContractSignature("p", ("first",), "", (), ("main", ()))
        second = ContractSignature("p", ("second",), "", (), ("main", ()))
        result = compare_contract_signatures((first, second), (second, first))
        self.assertTrue(result.passed)
        self.assertEqual(result.percentage, 100.0)

    def test_contract_excludes_only_the_declared_hidden_voice_visualizer(self) -> None:
        visualizer = (
            "bg-indigo-300/10 dark:bg-indigo-500/10 rounded-full flex "
            "justify-between p-2.5 w-full max-w-full"
        )
        left = (
            f'<html><body><main><div class="{visualizer}"><span class="bar"></span></div>'
            '<p class="answer"></p></main></body></html>'
        )
        right = '<html><body><main><p class="answer"></p></main></body></html>'
        self.assertTrue(compare_contract_html(left, right, scope="body").passed)
        included = compare_contract_html(
            left,
            right,
            scope="body",
            exclude_hidden_voice_visualizer=False,
        )
        self.assertFalse(included.passed)

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
            self.assertEqual(payload["comparison_mode"], "ordered_tag_class")

    def test_cli_contract_mode_reports_its_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.html"
            right = root / "right.html"
            left.write_text(
                '<html><body><main><button id="send" aria-label="Send"></button></main></body></html>',
                encoding="utf-8",
            )
            right.write_text(
                '<html><body><main><button id="send" title="Send"></button></main></body></html>',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("structure_compare.py")),
                    str(left),
                    str(right),
                    "--scope",
                    "body",
                    "--contract",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["comparison_mode"], "whole_element_contract")
            self.assertFalse(payload["passed"])


if __name__ == "__main__":
    unittest.main()
