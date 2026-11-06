# SPDX-License-Identifier: BSD-3-Clause
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from qualification.gate import evaluate, manifest_digest


class QualificationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "trace.json").write_text('{"synthetic":true}')
        self.manifest = {"schema_version": 1, "references": {"baseline": {
            "version": "synthetic", "image_digest": "sha256:" + "1" * 64,
            "inventory_sha256": "2" * 64, "operations": ["GET /synthetic"],
            "feature_inventory_complete": True, "configuration_inventory_complete": True,
        }}, "cases": [{"id": "session", "targets": ["baseline"], "required_levels": ["behavior", "journey", "restart"],
            "variants": [{"profile": "local", "role": "user", "state": "fresh"}]}]}
        self.run = {"schema_version": 1, "target": "baseline", "scope": "baseline", "candidate_digest": "sha256:" + "3" * 64,
            "reference_digest": "sha256:" + "1" * 64, "source_sha256": "4" * 64,
            "manifest_sha256": manifest_digest(self.manifest), "reference_inventory_sha256": "2" * 64,
            "findings_reviewed": True, "unresolved_critical_findings": [], "unresolved_major_findings": [], "records": []}
        for key in ["session/local/user/fresh", "operation/GET /synthetic"]:
            self.run["records"].append({"id": key, "status": "passed", "candidate_digest": self.run["candidate_digest"],
                "reference_digest": self.run["reference_digest"], "source_sha256": self.run["source_sha256"],
                "levels": ["routing", "behavior", "journey", "restart"], "oracle": "independent_black_box", "positive_control_passed": True,
                "evidence": [{"path": "trace.json", "sha256": hashlib.sha256((self.root / "trace.json").read_bytes()).hexdigest()}]})

    def check(self, manifest=None, run=None):
        return evaluate(manifest or self.manifest, run or self.run, self.root,
            expected_candidate="sha256:" + "3" * 64, expected_source="4" * 64)

    def test_old_run_cannot_qualify_another_requested_build(self):
        self.assertFalse(evaluate(self.manifest, self.run, self.root, expected_candidate="sha256:" + "5" * 64, expected_source="4" * 64)["qualified"])
        self.run["scope"] = "global"
        self.assertFalse(self.check()["qualified"])

    def test_complete_bound_synthetic_scope_qualifies(self):
        self.assertTrue(self.check()["qualified"])

    def test_unknown_skip_failure_or_unsupported_never_qualifies(self):
        for status in ["not_tested", "skipped", "failed", "unsupported", "infrastructure_error"]:
            run = copy.deepcopy(self.run); run["records"][0]["status"] = status
            self.assertFalse(self.check(run=run)["qualified"], status)

    def test_route_only_evidence_cannot_replace_a_journey_or_behavior(self):
        self.run["records"][0]["levels"] = ["inventory", "routing"]
        self.assertFalse(self.check()["qualified"])

    def test_wrong_artifact_source_target_manifest_or_missing_raw_trace_refuses(self):
        for field in ["candidate_digest", "reference_digest", "source_sha256"]:
            run = copy.deepcopy(self.run); run["records"][0][field] = "different"
            self.assertFalse(self.check(run=run)["qualified"], field)
        self.run["manifest_sha256"] = "old"
        self.assertFalse(self.check()["qualified"])
        self.run["manifest_sha256"] = manifest_digest(self.manifest)
        (self.root / "trace.json").write_text("changed")
        self.assertFalse(self.check()["qualified"])

    def test_missing_duplicate_and_unresolved_records_refuse(self):
        run = copy.deepcopy(self.run); run["records"].pop()
        self.assertFalse(self.check(run=run)["qualified"])
        run = copy.deepcopy(self.run); run["records"].append(run["records"][0])
        self.assertFalse(self.check(run=run)["qualified"])
        self.run["records"][0]["unresolved_effects"] = True
        self.assertFalse(self.check()["qualified"])

    def test_incomplete_feature_or_configuration_inventory_refuses(self):
        for field in ["feature_inventory_complete", "configuration_inventory_complete"]:
            manifest = copy.deepcopy(self.manifest); manifest["references"]["baseline"][field] = False
            run = copy.deepcopy(self.run); run["manifest_sha256"] = manifest_digest(manifest)
            self.assertFalse(self.check(manifest=manifest, run=run)["qualified"])

    def test_consumer_scope_cannot_reuse_an_unpinned_old_fixture(self):
        self.run["scope"] = "atlas"
        self.assertFalse(self.check()["qualified"])

    def test_incomplete_finding_review_and_duplicate_requirements_refuse(self):
        run = copy.deepcopy(self.run); run.pop("findings_reviewed")
        self.assertFalse(self.check(run=run)["qualified"])
        manifest = copy.deepcopy(self.manifest)
        manifest["cases"].append(copy.deepcopy(manifest["cases"][0]))
        run = copy.deepcopy(self.run); run["manifest_sha256"] = manifest_digest(manifest)
        self.assertFalse(self.check(manifest=manifest, run=run)["qualified"])
        manifest = copy.deepcopy(self.manifest)
        manifest["references"]["baseline"]["operations"].append("GET /synthetic")
        run = copy.deepcopy(self.run); run["manifest_sha256"] = manifest_digest(manifest)
        self.assertFalse(self.check(manifest=manifest, run=run)["qualified"])


if __name__ == "__main__":
    unittest.main()
