# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Build a deterministic HTTP operation inventory from a public OpenAPI contract."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options", "trace"}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def operations_from_openapi(contract):
    paths = contract.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI contract has no paths object")
    operations = []
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            raise ValueError("invalid OpenAPI path item")
        for method in path_item:
            if method.lower() in HTTP_METHODS:
                operations.append(f"{method.upper()} {path}")
    if len(operations) != len(set(operations)):
        raise ValueError("duplicate HTTP operation")
    return sorted(operations)


def route_domain(operation):
    path = operation.split(" ", 1)[1]
    mappings = (
        (("/api/v1/auths", "/oauth/", "/api/v1/users"), "identity"),
        (("/api/chat", "/api/v1/chats", "/api/tasks", "/api/v1/tasks"), "chat"),
        (("/api/v1/files", "/api/v1/retrieval", "/api/v1/utils"), "files"),
        (("/api/v1/knowledge", "/api/v1/memories"), "knowledge"),
        (("/api/v1/folders", "/api/v1/notes", "/api/v1/prompts"), "workspace"),
        (("/api/v1/channels", "/api/v1/comments", "/api/v1/calendars", "/api/v1/automations"), "collaboration"),
        (("/api/v1/functions", "/api/v1/tools", "/api/v1/pipelines", "/api/v1/skills", "/api/v1/terminals"), "extensions"),
        (("/openai", "/ollama", "/api/models", "/api/v1/models", "/api/embeddings"), "providers"),
        (("/api/v1/audio", "/api/v1/images"), "media"),
        (("/api/v1/configs", "/api/v1/analytics", "/api/v1/evaluations", "/api/v1/notifications"), "administration"),
    )
    for prefixes, domain in mappings:
        if path.startswith(prefixes):
            return domain
    return "core"


def comparison_report(contract_path, candidate, references, *, image_digest, source_sha256):
    report = {
        "schema_version": 1,
        "candidate": {
            "image_digest": image_digest,
            "source_sha256": source_sha256,
            "openapi": {
                "path": str(contract_path),
                "sha256": sha256_file(contract_path),
            },
            "operation_count": len(candidate),
            "operation_domains": dict(sorted(Counter(route_domain(item) for item in candidate).items())),
        },
        "references": {},
        "limits": "Declared route presence only; this does not establish authorization, response semantics, side effects, restart behavior, configuration availability or user-journey compatibility.",
    }
    candidate_set = set(candidate)
    for name, operations in references.items():
        reference_set = set(operations)
        missing = sorted(reference_set - candidate_set)
        extra = sorted(candidate_set - reference_set)
        report["references"][name] = {
            "operation_count": len(operations),
            "present_in_candidate_count": len(reference_set & candidate_set),
            "missing_from_candidate_count": len(missing),
            "candidate_only_count": len(extra),
            "missing_from_candidate_by_domain": dict(sorted(Counter(route_domain(item) for item in missing).items())),
            "missing_from_candidate": missing,
            "candidate_only": extra,
        }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--openapi", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--operations-output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--source-sha256", required=True)
    args = parser.parse_args()

    contract = json.loads(args.openapi.read_text())
    candidate = operations_from_openapi(contract)
    references = {
        "baseline": json.loads(args.baseline.read_text()),
        "current": json.loads(args.current.read_text()),
    }
    for name, operations in references.items():
        if operations != sorted(set(operations)):
            raise ValueError(f"{name} operation inventory is not sorted and unique")
    args.operations_output.write_text(json.dumps(candidate, indent=2) + "\n")
    report = comparison_report(
        args.openapi,
        candidate,
        references,
        image_digest=args.image_digest,
        source_sha256=args.source_sha256,
    )
    report["candidate"]["operation_inventory"] = {
        "path": str(args.operations_output),
        "sha256": sha256_file(args.operations_output),
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "candidate_operations": len(candidate),
        "baseline_missing": report["references"]["baseline"]["missing_from_candidate_count"],
        "current_missing": report["references"]["current"]["missing_from_candidate_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
