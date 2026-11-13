# SPDX-License-Identifier: BSD-3-Clause

import json
import tempfile
import unittest
from pathlib import Path

from validation.structure_gate import evaluate_manifest


class StructureGateTests(unittest.TestCase):
    def _fixture(self, root: Path, candidate: str) -> Path:
        reference = (
            '<html><body><main class="page"><button id="send" class="action" '
            'aria-label="Send"></button></main></body></html>'
        )
        (root / "reference-a.html").write_text(reference, encoding="utf-8")
        (root / "reference-b.html").write_text(reference, encoding="utf-8")
        (root / "candidate.html").write_text(candidate, encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target": "synthetic-reference",
                    "pages": [
                        {
                            "label": "desktop-home",
                            "reference_a": "reference-a.html",
                            "reference_b": "reference-b.html",
                            "candidate": "candidate.html",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_identical_candidate_and_paired_references_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._fixture(
                root,
                '<html><body><main class="page"><button class="action" id="send" '
                'aria-label="Envoyer"></button></main></body></html>',
            )
            report = evaluate_manifest(manifest)
            self.assertTrue(report["passed"])
            self.assertEqual(report["page_count"], 1)
            self.assertEqual(
                report["pages"][0]["reference_control"]["percentage"], 100.0
            )

    def test_attribute_contract_difference_fails_the_page_and_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._fixture(
                root,
                '<html><body><main class="page"><button id="send" class="action" '
                'title="Send"></button></main></body></html>',
            )
            report = evaluate_manifest(manifest)
            self.assertFalse(report["passed"])
            page = report["pages"][0]
            self.assertTrue(page["reference_control"]["passed"])
            self.assertFalse(page["candidate_vs_reference_a"]["passed"])

    def test_manifest_rejects_duplicate_labels_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._fixture(root, "<html><body></body></html>")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["pages"].append(dict(payload["pages"][0]))
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate page label"):
                evaluate_manifest(manifest)

            payload["pages"] = [dict(payload["pages"][0])]
            payload["pages"][0]["candidate"] = "../outside.html"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                evaluate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
