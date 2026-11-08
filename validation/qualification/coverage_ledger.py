# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Expand the finite qualification manifest without overstating coverage."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


OBSERVATION_STATES = {"partial", "failed", "blocked"}


def manifest_digest(manifest):
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def variant_key(case, variant):
    extra = [
        f"{field}={variant[field]}"
        for field in sorted(set(variant) - {"profile", "role", "state"})
    ]
    return "/".join(
        [case["id"], variant["profile"], variant["role"], variant["state"], *extra]
    )


def evidence_valid(root, item):
    if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
        return False
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts:
        return False
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file():
            return False
        if not path.resolve().is_relative_to(root.resolve()):
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    except OSError:
        return False


def operation_inventory_issues(reference, root):
    """Return provenance/integrity issues for a declared HTTP operation inventory."""
    issues = []
    operations = reference.get("operations")
    if not isinstance(operations, list) or not operations:
        return ["missing"]
    if (
        any(not isinstance(operation, str) or not operation for operation in operations)
        or operations != sorted(operations)
        or len(operations) != len(set(operations))
    ):
        issues.append("invalid_operations")

    inventory = reference.get("operation_inventory")
    if not isinstance(inventory, dict):
        return issues + ["provenance_incomplete"]
    artifact = inventory.get("artifact")
    if inventory.get("count") != len(operations):
        issues.append("count_mismatch")
    if not evidence_valid(root, artifact):
        issues.append("artifact_invalid")
    else:
        try:
            artifact_operations = json.loads((root / artifact["path"]).read_text())
        except (OSError, json.JSONDecodeError):
            artifact_operations = None
        if artifact_operations != operations:
            issues.append("artifact_content_mismatch")
    if (
        not isinstance(artifact, dict)
        or reference.get("inventory_sha256") != artifact.get("sha256")
    ):
        issues.append("digest_mismatch")
    if not inventory.get("provenance_complete", False):
        issues.append("provenance_incomplete")
    for field in ("source_contract", "generation_receipt"):
        if not evidence_valid(root, inventory.get(field)):
            issues.append(f"{field}_invalid")
    receipt_item = inventory.get("generation_receipt")
    if evidence_valid(root, receipt_item):
        try:
            receipt = json.loads((root / receipt_item["path"]).read_text())
        except (OSError, json.JSONDecodeError):
            receipt = None
        receipt_inventory = receipt.get("operation_inventory", {}) if isinstance(receipt, dict) else {}
        receipt_source = receipt.get("source_trace", {}) if isinstance(receipt, dict) else {}
        if (
            not isinstance(receipt, dict)
            or receipt.get("image_digest") != reference.get("image_digest")
            or receipt.get("config_digest") != reference.get("config_digest")
            or receipt.get("provenance_complete") is not True
            or receipt_inventory.get("sha256") != artifact.get("sha256")
            or receipt_inventory.get("count") != len(operations)
            or receipt_inventory.get("matches_regenerated_contract") is not True
        ):
            issues.append("generation_receipt_mismatch")
        source_item = inventory.get("source_contract")
        if (
            not isinstance(source_item, dict)
            or receipt_source.get("path") != source_item.get("path")
            or receipt_source.get("sha256") != source_item.get("sha256")
        ):
            issues.append("source_contract_mismatch")
    return sorted(set(issues))


def observation_reference_valid(root, item):
    if not isinstance(item, str):
        return False
    relative = Path(item)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        path = root / relative
        return (
            not path.is_symlink()
            and path.is_file()
            and path.resolve().is_relative_to(root.resolve())
        )
    except OSError:
        return False


def record_qualifies(record, case, root):
    return (
        record.get("status") == "passed"
        and set(case["required_levels"]) <= set(record.get("levels", []))
        and record.get("oracle") == "independent_black_box"
        and record.get("positive_control_passed") is True
        and not record.get("unresolved_effects")
        and bool(record.get("evidence"))
        and all(evidence_valid(root, item) for item in record["evidence"])
    )


def build_ledger(manifest, observations, run=None, evidence_root=Path(".")):
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("manifest cases must be a list")
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases):
        raise ValueError("duplicate case id")

    observation_rows = observations.get("family_observations", [])
    grouped = {case_id: [] for case_id in by_id}
    for row in observation_rows:
        case_id = row.get("case_id")
        if case_id not in by_id:
            raise ValueError(f"unknown observation case: {case_id}")
        if row.get("target") not in by_id[case_id]["targets"]:
            raise ValueError(f"invalid observation target: {case_id}")
        if row.get("state") not in OBSERVATION_STATES:
            raise ValueError(f"invalid observation state: {case_id}")
        if not row.get("evidence") or not all(
            observation_reference_valid(evidence_root, item)
            for item in row["evidence"]
        ):
            raise ValueError(f"observation lacks evidence: {case_id}")
        grouped[case_id].append(row)

    run_records = {}
    run_target = None
    if run is not None:
        run_target = run.get("target")
        if run_target not in manifest.get("references", {}):
            raise ValueError("run target is missing or unknown")
        for record in run.get("records", []):
            record_id = record.get("id")
            if not record_id or record_id in run_records:
                raise ValueError("duplicate or missing run record id")
            run_records[record_id] = record

    family_rows = []
    domain_totals = Counter()
    target_totals = Counter()
    target_qualified = Counter()
    qualified_total = 0
    for case in cases:
        target_counts = {}
        qualified_counts = {}
        for target in case["targets"]:
            keys = [variant_key(case, variant) for variant in case["variants"]]
            target_counts[target] = len(keys)
            target_totals[target] += len(keys)
            qualified = sum(
                1
                for key in keys
                if target == run_target
                and key in run_records
                and record_qualifies(run_records[key], case, evidence_root)
            )
            qualified_counts[target] = qualified
            target_qualified[target] += qualified
            qualified_total += qualified
        total = sum(target_counts.values())
        family_qualified = sum(qualified_counts.values())
        domain_totals[case["domain"]] += total
        family_rows.append(
            {
                "id": case["id"],
                "domain": case["domain"],
                "severity": case["severity"],
                "required_levels": case["required_levels"],
                "target_variants": target_counts,
                "qualified_variants": qualified_counts,
                "observations": grouped[case["id"]],
                "status": (
                    "qualified"
                    if family_qualified == total and total
                    else "observed_partial"
                    if grouped[case["id"]]
                    else "not_run"
                ),
            }
        )

    blockers = []
    for target, reference in manifest.get("references", {}).items():
        inventory_issues = operation_inventory_issues(reference, evidence_root)
        if "missing" in inventory_issues:
            blockers.append(f"{target}_operation_inventory_incomplete")
        if set(inventory_issues) - {"missing"}:
            blockers.append(f"{target}_operation_inventory_provenance_incomplete")
        if not reference.get("feature_inventory_complete", False):
            blockers.append(f"{target}_feature_inventory_incomplete")
        if not reference.get("configuration_inventory_complete", False):
            blockers.append(f"{target}_configuration_inventory_incomplete")
    if qualified_total < sum(target_totals.values()):
        blockers.append("required_target_variants_incomplete")

    return {
        "schema_version": 1,
        "manifest_sha256": manifest_digest(manifest),
        "summary": {
            "families": len(cases),
            "target_variants": sum(target_totals.values()),
            "qualified_target_variants": qualified_total,
            "observed_families": sum(bool(grouped[case_id]) for case_id in grouped),
            "target_totals": dict(sorted(target_totals.items())),
            "target_qualified": dict(sorted(target_qualified.items())),
            "domain_target_variants": dict(sorted(domain_totals.items())),
            "qualified": not blockers,
        },
        "blockers": blockers,
        "families": family_rows,
        "limits": (
            "Family observations are context only and never count as qualified variants. "
            "Only exact gate-compatible run records with bound evidence, required levels, "
            "an independent black-box oracle and a passing positive control can qualify."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--evidence-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ledger = build_ledger(
        json.loads(args.manifest.read_text()),
        json.loads(args.observations.read_text()),
        json.loads(args.run.read_text()) if args.run else None,
        args.evidence_root,
    )
    args.output.write_text(json.dumps(ledger, indent=2) + "\n")
    print(json.dumps(ledger["summary"], indent=2))


if __name__ == "__main__":
    main()
