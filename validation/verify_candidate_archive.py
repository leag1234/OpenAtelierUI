#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Verify an OpenAtelierUI Docker image archive without loading or extracting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import tarfile
from pathlib import Path
from typing import Any, BinaryIO


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024 * 1024
MAX_OUTER_MEMBERS = 100_000
MAX_LAYER_MEMBERS = 1_000_000
MAX_LAYERS = 128
MAX_JSON_MEMBERS = 32
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 32 * 1024 * 1024
MAX_BUFFERED_MEMBERS = 256
MAX_BUFFERED_BYTES = 64 * 1024 * 1024
MAX_LAYER_OPERATIONS = 65_536
MAX_REQUIRED_FILE_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

SOURCE_COPIES = {
    "opt/openatelierui/LICENSE": "LICENSE",
    "opt/openatelierui/NOTICE": "NOTICE",
    "opt/openatelierui/PROVENANCE.md": "PROVENANCE.md",
    "opt/openatelierui/migration/migrate_copy.py": "validation/migrate_atlas_copy.py",
    "opt/openatelierui/migration/migrate_sqlite.py": "validation/migrate_sqlite.py",
    "opt/openatelierui/migration/restore_archive.py": "validation/restore_v0113_archive.py",
    "usr/local/bin/openatelierui-migrate-v0113": "packaging/openatelierui-migrate-v0113",
    "usr/local/bin/openatelierui-restore-v0113": "packaging/openatelierui-restore-v0113",
    "app/backend/open_webui/main.py": "backend/open_webui/main.py",
    "app/build/pyodide/licenses/PYODIDE-0.27.3-LICENSE.txt": (
        "licenses/third-party/PYODIDE-0.27.3-LICENSE.txt"
    ),
    "app/build/pyodide/licenses/CPYTHON-3.12.7-LICENSE.txt": (
        "licenses/third-party/CPYTHON-3.12.7-LICENSE.txt"
    ),
    "app/build/pyodide/licenses/OPENBLAS-0.3.26-LICENSE.txt": (
        "licenses/third-party/OPENBLAS-0.3.26-LICENSE.txt"
    ),
}
GENERATED_REQUIRED = {
    "opt/openatelierui/THIRD_PARTY_NOTICES.frontend.txt",
    "opt/openatelierui/migration-template/webui.db",
    "app/build/index.html",
}
GENERATED_MINIMUM_BYTES = {
    "opt/openatelierui/THIRD_PARTY_NOTICES.frontend.txt": 1024,
    "opt/openatelierui/migration-template/webui.db": 512,
    "app/build/index.html": 32,
}
EXECUTABLE_REQUIRED = {
    "usr/local/bin/openatelierui-migrate-v0113",
    "usr/local/bin/openatelierui-restore-v0113",
}
REQUIRED_PATHS = frozenset(SOURCE_COPIES) | frozenset(GENERATED_REQUIRED)


class VerificationError(RuntimeError):
    """A fail-closed archive verification refusal."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class HashingReader:
    def __init__(self, source: BinaryIO):
        self.source = source
        self.digest = hashlib.sha256()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        payload = self.source.read(size)
        if payload:
            self.digest.update(payload)
            self.bytes_read += len(payload)
        return payload


def _safe_member_path(name: str) -> str:
    if not name or "\\" in name or name.startswith("/"):
        raise VerificationError("unsafe_member_path", name or "empty member name")
    relative = name
    while relative.startswith("./"):
        relative = relative[2:]
    normalized = posixpath.normpath(relative)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise VerificationError("unsafe_member_path", name)
    return normalized


def _remove_selected(state: dict[str, dict[str, Any]], target: str) -> None:
    prefix = target.rstrip("/") + "/"
    for path in list(state):
        if path == target or path.startswith(prefix):
            del state[path]


def _affects_required_path(target: str) -> bool:
    prefix = target.rstrip("/") + "/"
    return any(path == target or path.startswith(prefix) for path in REQUIRED_PATHS)


def _scan_layer(
    layer_file: BinaryIO,
) -> tuple[str, int, int, list[dict[str, Any]]]:
    reader = HashingReader(layer_file)
    member_count = 0
    operations: list[dict[str, Any]] = []

    def append_operation(operation: dict[str, Any]) -> None:
        operations.append(operation)
        if len(operations) > MAX_LAYER_OPERATIONS:
            raise VerificationError(
                "layer_operation_limit", f"more than {MAX_LAYER_OPERATIONS} operations"
            )

    try:
        with tarfile.open(fileobj=reader, mode="r|*") as layer:
            for member in layer:
                member_count += 1
                if member_count > MAX_LAYER_MEMBERS:
                    raise VerificationError(
                        "layer_member_limit", f"more than {MAX_LAYER_MEMBERS} members"
                    )
                path = _safe_member_path(member.name)
                basename = posixpath.basename(path)
                parent = posixpath.dirname(path)
                if basename == ".wh..wh..opq":
                    if not parent:
                        append_operation({"action": "clear"})
                    elif _affects_required_path(parent):
                        append_operation({"action": "opaque", "path": parent})
                    continue
                if basename.startswith(".wh."):
                    target = posixpath.join(parent, basename[len(".wh.") :])
                    if _affects_required_path(target):
                        append_operation({"action": "remove", "path": target})
                    continue
                if path not in REQUIRED_PATHS:
                    continue
                if not member.isfile():
                    member_kind = (
                        member.type.decode("ascii", "replace")
                        if isinstance(member.type, bytes)
                        else str(member.type)
                    )
                    append_operation(
                        {
                            "action": "set",
                            "path": path,
                            "item": {"type": "unsafe", "kind": member_kind},
                        }
                    )
                    continue
                if member.size <= 0 or member.size > MAX_REQUIRED_FILE_BYTES:
                    append_operation(
                        {
                            "action": "set",
                            "path": path,
                            "item": {"type": "invalid_size", "bytes": member.size},
                        }
                    )
                    continue
                extracted = layer.extractfile(member)
                if extracted is None:
                    append_operation(
                        {
                            "action": "set",
                            "path": path,
                            "item": {"type": "unreadable"},
                        }
                    )
                    continue
                payload = extracted.read(MAX_REQUIRED_FILE_BYTES + 1)
                if len(payload) != member.size:
                    append_operation(
                        {
                            "action": "set",
                            "path": path,
                            "item": {
                                "type": "size_mismatch",
                                "declared_bytes": member.size,
                                "read_bytes": len(payload),
                            },
                        }
                    )
                    continue
                append_operation(
                    {
                        "action": "set",
                        "path": path,
                        "item": {
                            "type": "file",
                            "bytes": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "mode": stat.S_IMODE(member.mode),
                            "prefix": payload[:4096],
                        },
                    }
                )
    except tarfile.TarError as exc:
        raise VerificationError("invalid_layer_tar", str(exc)) from exc

    while reader.read(CHUNK_BYTES):
        pass
    return (
        "sha256:" + reader.digest.hexdigest(),
        reader.bytes_read,
        member_count,
        operations,
    )


def _apply_layer_operations(
    operations: list[dict[str, Any]], state: dict[str, dict[str, Any]]
) -> None:
    for operation in operations:
        action = operation["action"]
        if action == "clear":
            state.clear()
        elif action == "opaque":
            parent = operation["path"].rstrip("/") + "/"
            for selected in list(state):
                if selected.startswith(parent):
                    del state[selected]
        elif action == "remove":
            _remove_selected(state, operation["path"])
        elif action == "set":
            state[operation["path"]] = operation["item"]
        else:  # pragma: no cover - operations are created internally
            raise VerificationError("invalid_layer_operation", str(action))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _read_source_hashes(source_root: Path) -> dict[str, dict[str, Any]]:
    hashes: dict[str, dict[str, Any]] = {}
    for image_path, relative in SOURCE_COPIES.items():
        source = source_root / relative
        try:
            source_stat = source.lstat()
        except FileNotFoundError as exc:
            raise VerificationError("missing_source_artifact", relative) from exc
        if not stat.S_ISREG(source_stat.st_mode):
            raise VerificationError("unsafe_source_artifact", relative)
        digest, size = _hash_file(source)
        if size <= 0 or size > MAX_REQUIRED_FILE_BYTES:
            raise VerificationError("invalid_source_artifact_size", relative)
        hashes[image_path] = {
            "source": relative,
            "bytes": size,
            "sha256": digest,
        }
    return hashes


def _json_member(payload: bytes, path: str) -> Any:
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("invalid_json_member", path) from exc


def verify_source_checkout(source_root: Path, expected_revision: str) -> dict[str, Any]:
    environment = {**os.environ, "LC_ALL": "C", "LANG": "C"}

    def run_git(*arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(source_root), *arguments],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError("source_git_unavailable", str(exc)) from exc

    revision = run_git("rev-parse", "--verify", "HEAD")
    if revision.returncode != 0:
        raise VerificationError(
            "source_git_unavailable", revision.stderr.strip() or "rev-parse failed"
        )
    actual_revision = revision.stdout.strip()
    if actual_revision != expected_revision:
        raise VerificationError(
            "source_revision_mismatch",
            f"expected {expected_revision}, got {actual_revision}",
        )

    status_result = run_git("status", "--porcelain=v1", "--untracked-files=all")
    if status_result.returncode != 0:
        raise VerificationError(
            "source_git_unavailable",
            status_result.stderr.strip() or "status failed",
        )
    dirty_entries = [line for line in status_result.stdout.splitlines() if line]
    if dirty_entries:
        raise VerificationError(
            "source_worktree_not_clean", f"{len(dirty_entries)} path(s)"
        )
    return {"revision": actual_revision, "worktree_clean": True}


def verify_archive(
    archive: Path, source_root: Path, expected_revision: str
) -> dict[str, Any]:
    if not archive.is_absolute() or not source_root.is_absolute():
        raise VerificationError("absolute_path_required", "archive and source root")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_revision):
        raise VerificationError("invalid_expected_revision", expected_revision)
    archive_stat = archive.lstat()
    if not stat.S_ISREG(archive_stat.st_mode):
        raise VerificationError("unsafe_archive", str(archive))
    if archive_stat.st_size <= 0 or archive_stat.st_size > MAX_ARCHIVE_BYTES:
        raise VerificationError("archive_size_limit", str(archive_stat.st_size))
    if not source_root.is_dir() or source_root.is_symlink():
        raise VerificationError("unsafe_source_root", str(source_root))

    source_hashes = _read_source_hashes(source_root)
    archive_sha256, archive_bytes = _hash_file(archive)
    initial_identity = (
        archive_stat.st_dev,
        archive_stat.st_ino,
        archive_stat.st_size,
        archive_stat.st_mtime_ns,
    )
    hashed_stat = archive.lstat()
    hashed_identity = (
        hashed_stat.st_dev,
        hashed_stat.st_ino,
        hashed_stat.st_size,
        hashed_stat.st_mtime_ns,
    )
    if hashed_identity != initial_identity:
        raise VerificationError("archive_changed_during_hash", str(archive))

    buffered_members: dict[str, bytes] = {}
    seen_outer: set[str] = set()
    outer_count = 0
    buffered_total_bytes = 0
    try:
        with tarfile.open(archive, mode="r|gz") as outer:
            for member in outer:
                outer_count += 1
                if outer_count > MAX_OUTER_MEMBERS:
                    raise VerificationError(
                        "outer_member_limit", f"more than {MAX_OUTER_MEMBERS} members"
                    )
                path = _safe_member_path(member.name)
                if path in seen_outer:
                    raise VerificationError("duplicate_outer_member", path)
                seen_outer.add(path)
                if not member.isfile():
                    continue
                extracted = outer.extractfile(member)
                if extracted is None:
                    raise VerificationError("unreadable_outer_member", path)
                is_named_json = path == "manifest.json" or path.endswith(".json")
                if is_named_json and (member.size <= 0 or member.size > MAX_JSON_BYTES):
                    raise VerificationError("json_member_size_limit", path)
                if 0 < member.size <= MAX_JSON_BYTES:
                    if len(buffered_members) >= MAX_BUFFERED_MEMBERS:
                        raise VerificationError(
                            "buffered_member_count_limit", str(MAX_BUFFERED_MEMBERS)
                        )
                    buffered_total_bytes += member.size
                    if buffered_total_bytes > MAX_BUFFERED_BYTES:
                        raise VerificationError(
                            "buffered_member_size_limit", str(buffered_total_bytes)
                        )
                    payload = extracted.read(member.size + 1)
                    if len(payload) != member.size:
                        raise VerificationError("buffered_member_size_mismatch", path)
                    buffered_members[path] = payload
    except (tarfile.ReadError, EOFError, OSError) as exc:
        raise VerificationError("invalid_gzip_docker_archive", str(exc)) from exc

    final_stat = archive.lstat()
    final_identity = (
        final_stat.st_dev,
        final_stat.st_ino,
        final_stat.st_size,
        final_stat.st_mtime_ns,
    )
    if final_identity != initial_identity:
        raise VerificationError("archive_changed_during_scan", str(archive))

    manifest_payload = buffered_members.get("manifest.json")
    if manifest_payload is None:
        raise VerificationError("missing_manifest", "manifest.json")
    manifest = _json_member(manifest_payload, "manifest.json")
    if not isinstance(manifest, list) or len(manifest) != 1:
        count = len(manifest) if isinstance(manifest, list) else "not-a-list"
        raise VerificationError("single_image_required", str(count))
    entry = manifest[0]
    if not isinstance(entry, dict):
        raise VerificationError("invalid_manifest_entry", "entry is not an object")
    config_path = entry.get("Config")
    declared_layers = entry.get("Layers")
    if not isinstance(config_path, str) or not isinstance(declared_layers, list):
        raise VerificationError("invalid_manifest_entry", "Config or Layers")
    if not all(isinstance(item, str) for item in declared_layers):
        raise VerificationError("invalid_manifest_layers", "non-string layer")
    if not 1 <= len(declared_layers) <= MAX_LAYERS:
        raise VerificationError("layer_count_limit", str(len(declared_layers)))
    normalized_layers = [_safe_member_path(item) for item in declared_layers]
    if normalized_layers != declared_layers or len(set(declared_layers)) != len(
        declared_layers
    ):
        raise VerificationError("invalid_manifest_layers", "unsafe or duplicate layer")
    missing_layers = [path for path in declared_layers if path not in seen_outer]
    if missing_layers:
        raise VerificationError("missing_declared_layer", missing_layers[0])

    config_path = _safe_member_path(config_path)
    config_payload = buffered_members.get(config_path)
    if config_payload is None:
        raise VerificationError("missing_configuration", config_path)
    config_sha256 = hashlib.sha256(config_payload).hexdigest()
    expected_config_name = (
        config_sha256
        if config_path.startswith("blobs/sha256/")
        else config_sha256 + ".json"
    )
    if posixpath.basename(config_path) != expected_config_name:
        raise VerificationError("configuration_digest_mismatch", config_path)
    config = _json_member(config_payload, config_path)
    if not isinstance(config, dict):
        raise VerificationError("invalid_configuration", "not an object")
    rootfs = config.get("rootfs")
    if not isinstance(rootfs, dict) or rootfs.get("type") != "layers":
        raise VerificationError("invalid_rootfs", "missing layers rootfs")
    diff_ids = rootfs.get("diff_ids")
    if not isinstance(diff_ids, list) or not all(
        isinstance(item, str) for item in diff_ids
    ):
        raise VerificationError("invalid_rootfs_diff_ids", "missing or malformed")

    layer_by_path: dict[str, dict[str, Any]] = {}
    declared_layer_set = set(declared_layers)
    try:
        with tarfile.open(archive, mode="r|gz") as outer:
            for member in outer:
                path = _safe_member_path(member.name)
                if path not in declared_layer_set:
                    continue
                if not member.isfile():
                    raise VerificationError("unsafe_declared_layer", path)
                extracted = outer.extractfile(member)
                if extracted is None:
                    raise VerificationError("unreadable_declared_layer", path)
                digest, layer_bytes, members, operations = _scan_layer(extracted)
                layer_by_path[path] = {
                    "path": path,
                    "diff_id": digest,
                    "bytes": layer_bytes,
                    "members": members,
                    "operations": operations,
                }
    except (tarfile.ReadError, EOFError, OSError) as exc:
        raise VerificationError("invalid_gzip_docker_archive", str(exc)) from exc

    layer_scan_stat = archive.lstat()
    layer_scan_identity = (
        layer_scan_stat.st_dev,
        layer_scan_stat.st_ino,
        layer_scan_stat.st_size,
        layer_scan_stat.st_mtime_ns,
    )
    if layer_scan_identity != initial_identity:
        raise VerificationError("archive_changed_during_layer_scan", str(archive))

    layer_results = [layer_by_path[path] for path in declared_layers]
    measured_diff_ids = [item["diff_id"] for item in layer_results]
    if measured_diff_ids != diff_ids:
        raise VerificationError("layer_digest_mismatch", "rootfs diff_ids")

    state: dict[str, dict[str, Any]] = {}
    for layer in layer_results:
        _apply_layer_operations(layer["operations"], state)

    image_config = config.get("config")
    if not isinstance(image_config, dict):
        raise VerificationError("invalid_runtime_configuration", "config")
    labels = image_config.get("Labels") or {}
    if not isinstance(labels, dict):
        raise VerificationError("invalid_labels", "Labels")
    required_labels = {
        "org.opencontainers.image.title": "OpenAtelierUI",
        "org.opencontainers.image.licenses": "BSD-3-Clause",
        "org.opencontainers.image.revision": expected_revision,
        "org.opencontainers.image.version": expected_revision,
    }
    for key, expected in required_labels.items():
        if labels.get(key) != expected:
            raise VerificationError(
                "label_mismatch", f"{key}: expected {expected!r}, got {labels.get(key)!r}"
            )

    artifacts: dict[str, dict[str, Any]] = {}
    for path in sorted(REQUIRED_PATHS):
        item = state.get(path)
        if item is None:
            raise VerificationError("missing_required_artifact", path)
        if item.get("type") != "file":
            raise VerificationError(
                "unsafe_required_artifact", f"{path}: {item.get('type')}"
            )
        if path in source_hashes:
            source_item = source_hashes[path]
            if (item["bytes"], item["sha256"]) != (
                source_item["bytes"],
                source_item["sha256"],
            ):
                raise VerificationError("source_artifact_mismatch", path)
        if path in EXECUTABLE_REQUIRED and not item["mode"] & 0o111:
            raise VerificationError("required_artifact_not_executable", path)
        minimum = GENERATED_MINIMUM_BYTES.get(path)
        if minimum is not None and item["bytes"] < minimum:
            raise VerificationError("generated_artifact_too_small", path)
        if path == "opt/openatelierui/migration-template/webui.db" and not item[
            "prefix"
        ].startswith(b"SQLite format 3\x00"):
            raise VerificationError("invalid_migration_template", path)
        if path == "app/build/index.html" and b"<html" not in item["prefix"].lower():
            raise VerificationError("invalid_built_frontend", path)
        artifacts[path] = {
            "bytes": item["bytes"],
            "sha256": item["sha256"],
            "mode": f"{item['mode']:04o}",
            **(
                {"source": source_hashes[path]["source"]}
                if path in source_hashes
                else {}
            ),
        }

    return {
        "schema_version": 1,
        "ok": True,
        "status": "verified_offline_candidate_archive",
        "archive": {
            "path": str(archive),
            "bytes": archive_bytes,
            "sha256": archive_sha256,
            "unchanged_during_scan": True,
        },
        "image": {
            "configuration_digest": "sha256:" + config_sha256,
            "repository_tags": entry.get("RepoTags", []),
            "layer_count": len(layer_results),
            "rootfs_diff_ids": measured_diff_ids,
            "labels": {key: labels[key] for key in sorted(required_labels)},
            "command": image_config.get("Cmd"),
            "user": image_config.get("User"),
            "healthcheck": image_config.get("Healthcheck"),
        },
        "required_artifacts": artifacts,
    }


def _write_new(path: Path, payload: str) -> None:
    if not path.is_absolute() or not path.parent.is_dir() or path.is_symlink():
        raise VerificationError("unsafe_output_path", str(path))
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise VerificationError("output_exists", str(path)) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify one gzip-compressed OpenAtelierUI Docker image archive offline."
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    try:
        source_identity = verify_source_checkout(
            arguments.source_root, arguments.expected_revision
        )
        result = verify_archive(
            arguments.archive, arguments.source_root, arguments.expected_revision
        )
        if verify_source_checkout(
            arguments.source_root, arguments.expected_revision
        ) != source_identity:
            raise VerificationError(
                "source_changed_during_scan", str(arguments.source_root)
            )
        result["source"] = source_identity
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if arguments.output is not None:
            _write_new(arguments.output, rendered)
        else:
            print(rendered, end="")
        return 0
    except (VerificationError, FileNotFoundError, PermissionError) as exc:
        reason = exc.reason if isinstance(exc, VerificationError) else "filesystem_error"
        detail = exc.detail if isinstance(exc, VerificationError) else str(exc)
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "ok": False,
                    "status": "refused_candidate_archive",
                    "reason": reason,
                    "detail": detail,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
