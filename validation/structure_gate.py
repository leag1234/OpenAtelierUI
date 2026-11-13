# SPDX-License-Identifier: BSD-3-Clause
"""Gate a fixed rendered-page set against two captures of one reference release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

if __package__:
    from .structure_compare import compare_contract_html
else:  # Support `python3 validation/structure_gate.py ...` from the repository root.
    from structure_compare import compare_contract_html


SCHEMA_VERSION = 1


def _resolve_regular_file(root: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} must be a non-empty relative path")
    relative = Path(raw)
    if relative.is_absolute():
        raise ValueError(f"{field} must be relative to the manifest")
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{field} escapes the manifest directory") from exc
    if not path.is_file():
        raise ValueError(f"{field} is not a regular file: {relative}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _comparison(left: Path, right: Path, threshold: float) -> dict[str, object]:
    result = compare_contract_html(
        left.read_text(encoding="utf-8", errors="replace"),
        right.read_text(encoding="utf-8", errors="replace"),
        scope="body",
        threshold=threshold,
    )
    return {
        "left_count": result.left_count,
        "right_count": result.right_count,
        "percentage": result.percentage,
        "passed": result.passed,
    }


def evaluate_manifest(manifest_path: Path, threshold: float = 99.0) -> dict[str, object]:
    if not 0.0 <= threshold <= 100.0:
        raise ValueError("threshold must be between 0 and 100")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    target = payload.get("target")
    if not isinstance(target, str) or not target.strip():
        raise ValueError("target must be a non-empty string")
    pages = payload.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("pages must be a non-empty array")

    root = manifest_path.resolve().parent
    labels: set[str] = set()
    reports: list[dict[str, object]] = []
    for index, item in enumerate(pages):
        if not isinstance(item, dict):
            raise ValueError(f"pages[{index}] must be an object")
        label = item.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"pages[{index}].label must be a non-empty string")
        if label in labels:
            raise ValueError(f"duplicate page label: {label}")
        labels.add(label)
        files = {
            field: _resolve_regular_file(root, item.get(field), f"{label}.{field}")
            for field in ("reference_a", "reference_b", "candidate")
        }
        reference_control = _comparison(
            files["reference_a"], files["reference_b"], threshold
        )
        candidate_a = _comparison(files["reference_a"], files["candidate"], threshold)
        candidate_b = _comparison(files["reference_b"], files["candidate"], threshold)
        passed = bool(
            reference_control["passed"]
            and candidate_a["passed"]
            and candidate_b["passed"]
        )
        reports.append(
            {
                "label": label,
                "passed": passed,
                "files": {
                    name: {
                        "path": str(path.relative_to(root)),
                        "sha256": _sha256(path),
                    }
                    for name, path in files.items()
                },
                "reference_control": reference_control,
                "candidate_vs_reference_a": candidate_a,
                "candidate_vs_reference_b": candidate_b,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "comparison_mode": "whole_element_contract",
        "target": target,
        "threshold": threshold,
        "page_count": len(reports),
        "passed": all(bool(report["passed"]) for report in reports),
        "pages": reports,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--threshold", type=float, default=99.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = evaluate_manifest(args.manifest, args.threshold)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
