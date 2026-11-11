#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from validation import verify_candidate_archive as verifier


ROOT = Path(__file__).resolve().parents[1]
REVISION = "1" * 40


def tar_bytes(entries: list[tuple[str, bytes, int, str]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path, payload, mode, kind in entries:
            info = tarfile.TarInfo(path)
            info.mode = mode
            if kind == "file":
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload.decode("utf-8")
                archive.addfile(info)
            else:
                raise AssertionError(kind)
    return buffer.getvalue()


def required_entries(source_root: Path = ROOT) -> list[tuple[str, bytes, int, str]]:
    entries: list[tuple[str, bytes, int, str]] = []
    for image_path, relative in verifier.SOURCE_COPIES.items():
        mode = 0o555 if image_path in verifier.EXECUTABLE_REQUIRED else 0o444
        entries.append((image_path, (source_root / relative).read_bytes(), mode, "file"))
    generated = {
        "opt/openatelierui/THIRD_PARTY_NOTICES.frontend.txt": b"notice\n" + b"n" * 2048,
        "opt/openatelierui/migration-template/webui.db": (
            b"SQLite format 3\x00" + b"\x00" * 496
        ),
        "app/build/index.html": b"<!doctype html><html><body>candidate</body></html>",
    }
    for image_path, payload in generated.items():
        entries.append((image_path, payload, 0o444, "file"))
    return entries


def build_archive(
    path: Path,
    *,
    revision: str = REVISION,
    mutate_entries=None,
    wrong_config_name: bool = False,
    wrong_diff_id: bool = False,
    image_count: int = 1,
    source_root: Path = ROOT,
    oci_blob_paths: bool = False,
) -> None:
    first_entries = required_entries(source_root)
    if mutate_entries is not None:
        first_entries = mutate_entries(first_entries)
    layer_one = tar_bytes(first_entries)
    layer_two = tar_bytes(
        [
            (
                "usr/local/bin/.wh.openatelierui-restore-v0113",
                b"",
                0o000,
                "file",
            ),
            (
                "usr/local/bin/openatelierui-restore-v0113",
                (source_root / "packaging/openatelierui-restore-v0113").read_bytes(),
                0o555,
                "file",
            ),
        ]
    )
    layers = [layer_one, layer_two]
    diff_ids = ["sha256:" + hashlib.sha256(payload).hexdigest() for payload in layers]
    layer_names = (
        ["blobs/sha256/" + digest.removeprefix("sha256:") for digest in diff_ids]
        if oci_blob_paths
        else [f"layer-{index}/layer.tar" for index in range(len(layers))]
    )
    if wrong_diff_id:
        diff_ids[-1] = "sha256:" + "0" * 64
    config = {
        "config": {
            "Labels": {
                "org.opencontainers.image.title": "OpenAtelierUI",
                "org.opencontainers.image.licenses": "BSD-3-Clause",
                "org.opencontainers.image.revision": revision,
                "org.opencontainers.image.version": revision,
            },
            "Cmd": ["bash", "start.sh"],
            "User": "0:0",
            "Healthcheck": {"Test": ["CMD", "true"]},
        },
        "rootfs": {"type": "layers", "diff_ids": diff_ids},
    }
    config_payload = json.dumps(config, sort_keys=True).encode()
    config_digest = hashlib.sha256(config_payload).hexdigest()
    config_name = (
        "blobs/sha256/" + config_digest
        if oci_blob_paths
        else config_digest + ".json"
    )
    if wrong_config_name:
        config_name = "0" * 64 + ".json"
    manifest_entry = {
        "Config": config_name,
        "RepoTags": ["openatelierui:test"],
        "Layers": layer_names,
    }
    manifest = [manifest_entry for _ in range(image_count)]
    members = [
            (config_name, config_payload),
            *zip(layer_names, layers),
            ("manifest.json", json.dumps(manifest).encode()),
        ]
    if oci_blob_paths:
        members = sorted(members[:-1]) + members[-1:]
    with tarfile.open(path, mode="w:gz") as outer:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.mode = 0o444
            info.size = len(payload)
            outer.addfile(info, io.BytesIO(payload))


class CandidateArchiveVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.archive = self.directory / "candidate.tar.gz"

    def clean_source_checkout(self) -> tuple[Path, str]:
        source = self.directory / "source"
        for relative in sorted(set(verifier.SOURCE_COPIES.values())):
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        commands = [
            ["git", "init", "-q"],
            ["git", "config", "user.name", "Archive verifier test"],
            ["git", "config", "user.email", "archive-verifier@example.invalid"],
            ["git", "add", "."],
            ["git", "commit", "-q", "-m", "Synthetic source"],
        ]
        for command in commands:
            completed = subprocess.run(
                command, cwd=source, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        return source, revision

    def test_verified_archive_binds_identity_layers_and_required_artifacts(self):
        build_archive(self.archive)
        result = verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "verified_offline_candidate_archive")
        self.assertEqual(result["image"]["layer_count"], 2)
        self.assertEqual(len(result["required_artifacts"]), len(verifier.REQUIRED_PATHS))
        self.assertEqual(
            result["image"]["labels"]["org.opencontainers.image.revision"],
            REVISION,
        )
        self.assertTrue(result["archive"]["unchanged_during_scan"])

    def test_oci_blob_layers_before_manifest_are_verified_in_declared_order(self):
        build_archive(self.archive, oci_blob_paths=True)
        result = verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertTrue(result["ok"])
        self.assertEqual(result["image"]["layer_count"], 2)
        self.assertEqual(
            result["image"]["rootfs_diff_ids"],
            [
                "sha256:" + hashlib.sha256(payload).hexdigest()
                for payload in [
                    tar_bytes(required_entries(ROOT)),
                    tar_bytes(
                        [
                            (
                                "usr/local/bin/.wh.openatelierui-restore-v0113",
                                b"",
                                0o000,
                                "file",
                            ),
                            (
                                "usr/local/bin/openatelierui-restore-v0113",
                                (ROOT / "packaging/openatelierui-restore-v0113").read_bytes(),
                                0o555,
                                "file",
                            ),
                        ]
                    ),
                ]
            ],
        )

    def test_wrong_revision_is_refused(self):
        build_archive(self.archive, revision="2" * 40)
        with self.assertRaisesRegex(verifier.VerificationError, "revision") as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "label_mismatch")

    def test_configuration_filename_must_match_content_digest(self):
        build_archive(self.archive, wrong_config_name=True)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "configuration_digest_mismatch")

    def test_every_layer_must_match_rootfs_diff_id(self):
        build_archive(self.archive, wrong_diff_id=True)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "layer_digest_mismatch")

    def test_whiteout_of_required_artifact_is_refused(self):
        def mutate(entries):
            return entries + [
                ("opt/openatelierui/.wh.NOTICE", b"", 0o000, "file")
            ]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "missing_required_artifact")

    def test_root_opaque_marker_removes_prior_required_state(self):
        def mutate(entries):
            return entries + [(".wh..wh..opq", b"", 0o000, "file")]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "missing_required_artifact")

    def test_parent_traversal_member_name_is_refused(self):
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier._safe_member_path("../../opt/openatelierui/LICENSE")
        self.assertEqual(caught.exception.reason, "unsafe_member_path")

    def test_required_symlink_is_refused(self):
        def mutate(entries):
            return [
                (path, b"LICENSE", mode, "symlink")
                if path == "opt/openatelierui/NOTICE"
                else (path, payload, mode, kind)
                for path, payload, mode, kind in entries
            ]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "unsafe_required_artifact")

    def test_source_copy_byte_mismatch_is_refused(self):
        def mutate(entries):
            return [
                (path, b"wrong", mode, kind)
                if path == "opt/openatelierui/PROVENANCE.md"
                else (path, payload, mode, kind)
                for path, payload, mode, kind in entries
            ]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "source_artifact_mismatch")

    def test_multi_image_archive_is_refused(self):
        build_archive(self.archive, image_count=2)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "single_image_required")

    def test_non_executable_operator_command_is_refused(self):
        def mutate(entries):
            return [
                (path, payload, 0o444, kind)
                if path == "usr/local/bin/openatelierui-migrate-v0113"
                else (path, payload, mode, kind)
                for path, payload, mode, kind in entries
            ]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "required_artifact_not_executable")

    def test_non_sqlite_migration_template_is_refused(self):
        def mutate(entries):
            return [
                (path, b"x" * 512, mode, kind)
                if path == "opt/openatelierui/migration-template/webui.db"
                else (path, payload, mode, kind)
                for path, payload, mode, kind in entries
            ]

        build_archive(self.archive, mutate_entries=mutate)
        with self.assertRaises(verifier.VerificationError) as caught:
            verifier.verify_archive(self.archive, ROOT, REVISION)
        self.assertEqual(caught.exception.reason, "invalid_migration_template")

    def test_cli_writes_new_private_receipt_and_refuses_overwrite(self):
        source, revision = self.clean_source_checkout()
        build_archive(self.archive, source_root=source, revision=revision)
        output = self.directory / "receipt.json"
        command = [
            sys.executable,
            str(ROOT / "validation/verify_candidate_archive.py"),
            "--archive",
            str(self.archive),
            "--source-root",
            str(source),
            "--expected-revision",
            revision,
            "--output",
            str(output),
        ]
        first = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr or first.stdout)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        receipt = json.loads(output.read_text())
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["source"]["revision"], revision)
        self.assertTrue(receipt["source"]["worktree_clean"])
        second = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stdout)["reason"], "output_exists")

    def test_source_revision_and_cleanliness_are_enforced(self):
        source, revision = self.clean_source_checkout()
        self.assertEqual(
            verifier.verify_source_checkout(source, revision),
            {"revision": revision, "worktree_clean": True},
        )
        with self.assertRaises(verifier.VerificationError) as revision_error:
            verifier.verify_source_checkout(source, "0" * 40)
        self.assertEqual(revision_error.exception.reason, "source_revision_mismatch")
        (source / "untracked.txt").write_text("dirty", encoding="utf-8")
        with self.assertRaises(verifier.VerificationError) as dirty_error:
            verifier.verify_source_checkout(source, revision)
        self.assertEqual(dirty_error.exception.reason, "source_worktree_not_clean")


if __name__ == "__main__":
    unittest.main(verbosity=2)
