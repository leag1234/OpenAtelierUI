#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Restore a verified copy-only migration archive into a fresh data root."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import time
from typing import Callable
from urllib.parse import quote
import uuid


MAX_DATABASE_BYTES = 8 * 1024 * 1024 * 1024
MAX_UPLOAD_BYTES = 512 * 1024 * 1024 * 1024
MAX_UPLOAD_FILES = 250_000
CHUNK = 1024 * 1024


class Refusal(Exception):
    def __init__(self, code: str, **details):
        super().__init__(code)
        self.code = code
        self.details = details


def _hash_regular(path: Path, limit: int) -> tuple[int, str, tuple[int, int, int, int]]:
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise Refusal("unsafe_archive")
        if before.st_size > limit:
            raise Refusal("resource_limit", measured_bytes=before.st_size, limit_bytes=limit)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        digest = hashlib.sha256()
        total = 0
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise Refusal("archive_changed")
            while True:
                block = os.read(descriptor, CHUNK)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise Refusal("resource_limit", measured_bytes=total, limit_bytes=limit)
                digest.update(block)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        current = path.lstat()
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise Refusal("archive_changed")
        if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise Refusal("archive_changed")
        return total, digest.hexdigest(), identity
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("unsafe_archive") from None


def _archive_directory(value: str | Path) -> Path:
    path = Path(value)
    try:
        if not path.is_absolute() or ".." in path.parts:
            raise Refusal("unsafe_archive")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Refusal("unsafe_archive")
        return path.resolve(strict=True)
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("unsafe_archive") from None


def _upload_manifest(root: Path) -> tuple[list[tuple], int, str]:
    try:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise Refusal("unsafe_upload_archive")
        entries = []
        total = 0
        for entry in sorted(os.scandir(root), key=lambda item: item.name):
            try:
                encoded = entry.name.encode("utf-8")
            except UnicodeError:
                raise Refusal("unsafe_upload_archive") from None
            if not encoded or len(encoded) > 1024:
                raise Refusal("unsafe_upload_archive")
            path = Path(entry.path)
            size, digest, identity = _hash_regular(path, MAX_UPLOAD_BYTES)
            total += size
            if len(entries) >= MAX_UPLOAD_FILES:
                raise Refusal(
                    "upload_file_count_limit",
                    measured_files=len(entries) + 1,
                    upload_file_limit=MAX_UPLOAD_FILES,
                )
            if total > MAX_UPLOAD_BYTES:
                raise Refusal(
                    "upload_size_limit",
                    measured_upload_bytes=total,
                    upload_limit_bytes=MAX_UPLOAD_BYTES,
                )
            entries.append((entry.name, size, digest, identity))
        combined = hashlib.sha256()
        for name, size, digest, _identity in entries:
            encoded = name.encode("utf-8")
            combined.update(
                len(encoded).to_bytes(4, "big")
                + encoded
                + size.to_bytes(8, "big")
                + bytes.fromhex(digest)
            )
        return entries, total, combined.hexdigest()
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("unsafe_upload_archive") from None


def _check_sqlite(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise Refusal("sqlite_sidecar_unsupported")
    try:
        uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
        database = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            database.execute("PRAGMA query_only=ON")
            if database.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise Refusal("corrupt_archive_database")
        finally:
            database.close()
    except Refusal:
        raise
    except sqlite3.Error:
        raise Refusal("corrupt_archive_database") from None


def _fresh_target(value: str | Path, archive: Path) -> Path:
    path = Path(value)
    try:
        if not path.is_absolute() or ".." in path.parts or path.exists() or path.is_symlink():
            raise Refusal("target_not_fresh")
        parent = path.parent.resolve(strict=True)
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise Refusal("unsafe_target_parent")
        target = parent / path.name
        if target == archive or archive in target.parents or target in archive.parents:
            raise Refusal("path_overlap")
        return target
    except Refusal:
        raise
    except (OSError, ValueError):
        raise Refusal("unsafe_target_parent") from None


def _copy_regular(source: Path, destination: Path, expected_size: int, expected_hash: str) -> None:
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o600)
    size, digest, _identity = _hash_regular(destination, max(expected_size, 1))
    if size != expected_size or digest != expected_hash:
        raise Refusal("copy_verification_failed")


def _private_tree(root: Path) -> None:
    for parent, directories, files in os.walk(root):
        os.chmod(parent, 0o700)
        for name in directories:
            os.chmod(Path(parent) / name, 0o700)
        for name in files:
            path = Path(parent) / name
            if not stat.S_ISREG(path.lstat().st_mode):
                raise Refusal("unsafe_restored_artifact")
            os.chmod(path, 0o600)


def _publish_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a directory without replacing a competing path."""
    library = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_target = os.fsencode(target)
    if sys.platform == "linux":
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise Refusal("atomic_publish_unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, encoded_source, -100, encoded_target, 1)
    elif sys.platform == "darwin":
        rename = getattr(library, "renamex_np", None)
        if rename is None:
            raise Refusal("atomic_publish_unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(encoded_source, encoded_target, 0x00000004)
    else:
        raise Refusal("atomic_publish_unavailable")
    if result != 0:
        error = ctypes.get_errno()
        if error in (errno.EEXIST, errno.ENOTEMPTY):
            raise Refusal("target_not_fresh")
        raise OSError(error, os.strerror(error), str(target))


def restore(
    archive_dir: str | Path,
    new_target_dir: str | Path,
    *,
    failpoint: Callable[[str], None] | None = None,
) -> dict:
    started = time.perf_counter()
    archive = _archive_directory(archive_dir)
    target = _fresh_target(new_target_dir, archive)
    source = archive / "source.sqlite"
    uploads = archive / "uploads"
    database_bytes, database_hash, database_identity = _hash_regular(source, MAX_DATABASE_BYTES)
    _check_sqlite(source)
    upload_entries, upload_bytes, upload_manifest_hash = _upload_manifest(uploads)
    required_bytes = database_bytes + upload_bytes + 64 * 1024 * 1024
    free_bytes = shutil.disk_usage(target.parent).free
    if free_bytes < required_bytes:
        raise Refusal(
            "insufficient_free_space",
            measured_free_bytes=free_bytes,
            required_free_bytes=required_bytes,
        )

    attempt = target.parent / ("." + target.name + ".restore-attempt-" + uuid.uuid4().hex)
    attempt.mkdir(mode=0o700)
    try:
        _copy_regular(source, attempt / "webui.db", database_bytes, database_hash)
        restored_uploads = attempt / "uploads"
        restored_uploads.mkdir(mode=0o700)
        for name, size, digest, _identity in upload_entries:
            _copy_regular(uploads / name, restored_uploads / name, size, digest)
        _check_sqlite(attempt / "webui.db")
        restored_entries, restored_bytes, restored_manifest_hash = _upload_manifest(restored_uploads)
        if (
            [(row[0], row[1], row[2]) for row in restored_entries],
            restored_bytes,
            restored_manifest_hash,
        ) != (
            [(row[0], row[1], row[2]) for row in upload_entries],
            upload_bytes,
            upload_manifest_hash,
        ):
            raise Refusal("copy_verification_failed")
        if _hash_regular(source, MAX_DATABASE_BYTES) != (
            database_bytes,
            database_hash,
            database_identity,
        ) or _upload_manifest(uploads) != (upload_entries, upload_bytes, upload_manifest_hash):
            raise Refusal("archive_changed")
        if target.exists() or target.is_symlink():
            raise Refusal("target_not_fresh")
        if failpoint:
            failpoint("before_publish")
        _private_tree(attempt)
        _publish_directory_no_replace(attempt, target)
        return {
            "ok": True,
            "status": "restored_copy_only",
            "cutover": False,
            "target_created": True,
            "target_dir": str(target),
            "database_bytes": database_bytes,
            "database_sha256": database_hash,
            "upload_count": len(upload_entries),
            "upload_bytes": upload_bytes,
            "uploads_manifest_sha256": upload_manifest_hash,
            "archive_unchanged": True,
            "elapsed_seconds": round(time.perf_counter() - started, 6),
        }
    except Exception:
        if attempt.exists() and attempt.is_dir() and not attempt.is_symlink():
            shutil.rmtree(attempt)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--new-target-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = restore(args.archive_dir, args.new_target_dir)
    except Refusal as error:
        result = {
            "ok": False,
            "status": "refused_input",
            "reason": error.code,
            "cutover": False,
            "target_created": False,
            **error.details,
        }
    except (OSError, ValueError):
        result = {
            "ok": False,
            "status": "refused_input",
            "reason": "restore_unavailable",
            "cutover": False,
            "target_created": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
