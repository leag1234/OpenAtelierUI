# SPDX-License-Identifier: BSD-3-Clause
"""Fail-closed qualification accounting. This is not execution authority or proof of zero defects."""
import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from .coverage_ledger import operation_inventory_issues
except ImportError:  # pragma: no cover - direct script execution
    from coverage_ledger import operation_inventory_issues

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def manifest_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def case_variants(manifest, target):
    for case in manifest["cases"]:
        if target not in case["targets"]:
            continue
        for variant in case["variants"]:
            extra = [f"{field}={variant[field]}" for field in sorted(set(variant) - {"profile", "role", "state"})]
            key = "/".join([case["id"], variant["profile"], variant["role"], variant["state"], *extra])
            yield key, case, variant


def evidence_valid(root, item):
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        return False
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts:
        return False
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(65536), b""):
                digest.update(block)
        return digest.hexdigest() == item["sha256"]
    except OSError:
        return False


def evaluate(manifest, run, root, *, expected_candidate, expected_source):
    """Every required row must have fresh, bound evidence; unknown/skip/unsupported is not pass."""
    blockers = []
    target = run.get("target")
    reference = manifest.get("references", {}).get(target)
    if manifest.get("schema_version") != 1 or run.get("schema_version") != 1 or not reference:
        return {"qualified": False, "blockers": ["invalid_schema_or_target"]}
    if run.get("manifest_sha256") != manifest_digest(manifest):
        blockers.append("stale_or_different_manifest")
    candidate = run.get("candidate_digest", "")
    if not DIGEST.fullmatch(candidate) or not re.fullmatch(r"[0-9a-f]{64}", run.get("source_sha256", "")):
        blockers.append("unbound_candidate_or_source")
    expected_reference = reference.get("image_digest")
    if not expected_reference or run.get("reference_digest") != expected_reference:
        blockers.append("unbound_or_wrong_reference")
    inventory_issues = operation_inventory_issues(reference, root)
    if "missing" in inventory_issues:
        blockers.append("reference_inventory_incomplete")
    if set(inventory_issues) - {"missing"}:
        blockers.append("reference_inventory_provenance_incomplete")
    if not reference.get("feature_inventory_complete", False):
        blockers.append("non_http_feature_inventory_incomplete")
    if not reference.get("configuration_inventory_complete", False):
        blockers.append("configuration_inventory_incomplete")
    if run.get("scope") == "atlas":
        pinned = manifest.get("consumer_current_revision")
        if not pinned or run.get("consumer_revision") != pinned:
            blockers.append("current_consumer_revision_unpinned_or_different")
    scope = run.get("scope")
    if scope not in {"baseline", "current", "atlas"} or (scope in {"baseline", "atlas"} and target != "baseline") or (scope == "current" and target != "current"):
        blockers.append("invalid_or_mismatched_claim_scope")
    if candidate != expected_candidate or run.get("source_sha256") != expected_source:
        blockers.append("evidence_does_not_match_requested_build")
    if run.get("reference_inventory_sha256") != reference.get("inventory_sha256"):
        blockers.append("wrong_reference_inventory")
    records = run.get("records", [])
    indexed = {}
    for record in records:
        key = record.get("id")
        if not key or key in indexed:
            blockers.append("duplicate_or_missing_record_id")
        indexed[key] = record
    required = {}
    for key, case, _ in case_variants(manifest, target):
        if key in required:
            blockers.append("duplicate_required_scenario:" + key)
        required[key] = case
    if not required:
        blockers.append("empty_required_scenario_scope")
    for operation in reference.get("operations") or []:
        key = "operation/" + operation
        if key in required:
            blockers.append("duplicate_required_operation:" + operation)
        required[key] = {"required_levels": ["routing", "behavior"]}
    extra = set(indexed) - set(required)
    if extra:
        blockers.append("unknown_record_ids:" + str(len(extra)))
    passed = 0
    missing = 0
    failed = 0
    for key, case in required.items():
        record = indexed.get(key)
        if record is None:
            missing += 1
            continue
        reasons = []
        if record.get("status") != "passed":
            reasons.append("not_passed")
        if record.get("candidate_digest") != candidate or record.get("reference_digest") != expected_reference:
            reasons.append("wrong_artifact")
        if record.get("source_sha256") != run.get("source_sha256"):
            reasons.append("wrong_source")
        if not set(case["required_levels"]) <= set(record.get("levels", [])):
            reasons.append("insufficient_evidence_level")
        if record.get("oracle") != "independent_black_box" or record.get("positive_control_passed") is not True:
            reasons.append("oracle_or_positive_control_missing")
        artifacts = record.get("evidence", [])
        if not artifacts or not all(evidence_valid(root, artifact) for artifact in artifacts):
            reasons.append("missing_or_changed_raw_evidence")
        if record.get("unresolved_effects"):
            reasons.append("unresolved_effects")
        if reasons:
            failed += 1
            blockers.append(key + ":" + ",".join(reasons))
        else:
            passed += 1
    if missing:
        blockers.append("required_records_missing:" + str(missing))
    if run.get("findings_reviewed") is not True or any(not isinstance(run.get(field), list) for field in ["unresolved_critical_findings", "unresolved_major_findings"]):
        blockers.append("finding_review_missing_or_invalid")
    if run.get("unresolved_critical_findings") or run.get("unresolved_major_findings"):
        blockers.append("unresolved_critical_or_major_findings")
    return {
        "qualified": not blockers, "target": target, "version": reference["version"],
        "candidate_digest": candidate, "manifest_sha256": manifest_digest(manifest),
        "required": len(required), "passed": passed, "missing": missing, "failed": failed,
        "blockers": blockers,
        "limits": "Evidence bindings and declared coverage only; oracle semantics, undiscovered features and human comprehension still require review. No universal defect-free guarantee.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--candidate-digest", required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    run = json.loads(args.run.read_text())
    report = evaluate(manifest, run, args.run.parent, expected_candidate=args.candidate_digest, expected_source=args.source_sha256)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "blockers"}, indent=2))
    print("QUALIFIED FOR DECLARED SCOPE" if report["qualified"] else "NOT QUALIFIED")
    raise SystemExit(0 if report["qualified"] else 2)


if __name__ == "__main__":
    main()
