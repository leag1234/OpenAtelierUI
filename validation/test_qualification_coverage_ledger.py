# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from validation.qualification.coverage_ledger import build_ledger, variant_key


def fixture_manifest():
    case = {
        "id": "chat.basic",
        "domain": "chat",
        "targets": ["baseline", "current"],
        "required_levels": ["behavior", "journey"],
        "variants": [
            {"profile": "isolated", "role": "user", "state": "fresh"},
            {"profile": "isolated", "role": "admin", "state": "migrated"},
        ],
        "severity": "critical",
    }
    return {
        "schema_version": 1,
        "references": {
            "baseline": {
                "operations": ["GET /health"],
                "feature_inventory_complete": False,
                "configuration_inventory_complete": False,
            },
            "current": {
                "operations": None,
                "feature_inventory_complete": False,
                "configuration_inventory_complete": False,
            },
        },
        "cases": [case],
    }


class CoverageLedgerTests(unittest.TestCase):
    def test_partial_observation_never_qualifies_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "synthetic-trace.json").write_text("{}\n")
            ledger = build_ledger(
                fixture_manifest(),
                {
                    "family_observations": [
                        {
                            "case_id": "chat.basic",
                            "target": "baseline",
                            "state": "partial",
                            "evidence": ["synthetic-trace.json"],
                        }
                    ]
                },
                evidence_root=root,
            )
        self.assertEqual(ledger["summary"]["target_variants"], 4)
        self.assertEqual(ledger["summary"]["qualified_target_variants"], 0)
        self.assertEqual(ledger["families"][0]["status"], "observed_partial")

    def test_exact_bound_record_can_qualify_only_its_variant(self):
        manifest = fixture_manifest()
        case = manifest["cases"][0]
        key = variant_key(case, case["variants"][0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "trace.json"
            evidence.write_text("{}\n")
            run = {
                "target": "baseline",
                "records": [
                    {
                        "id": key,
                        "status": "passed",
                        "levels": ["behavior", "journey"],
                        "oracle": "independent_black_box",
                        "positive_control_passed": True,
                        "unresolved_effects": [],
                        "evidence": [
                            {
                                "path": "trace.json",
                                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                            }
                        ],
                    }
                ]
            }
            ledger = build_ledger(
                manifest, {"family_observations": []}, run, root
            )
        self.assertEqual(ledger["summary"]["qualified_target_variants"], 1)
        self.assertEqual(ledger["families"][0]["qualified_variants"]["baseline"], 1)
        self.assertEqual(ledger["families"][0]["qualified_variants"]["current"], 0)

    def test_unknown_observation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown observation case"):
            build_ledger(
                fixture_manifest(),
                {
                    "family_observations": [
                        {
                            "case_id": "unknown.case",
                            "target": "baseline",
                            "state": "partial",
                            "evidence": ["trace.json"],
                        }
                    ]
                },
            )


if __name__ == "__main__":
    unittest.main()
