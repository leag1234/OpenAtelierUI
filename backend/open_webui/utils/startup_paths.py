# Copyright (c) 2026 Gaël Duval
# SPDX-License-Identifier: BSD-3-Clause

"""Small, dependency-free startup path checks for hardened deployments."""

import os
import shutil
import tempfile
from pathlib import Path


class StartupPathError(RuntimeError):
    """An operator-actionable startup path configuration error."""


def ensure_implicit_data_directory(data_dir: Path) -> Path:
    """Create and validate the directory used by the implicit SQLite URL."""
    data_dir = Path(data_dir)
    message = (
        f"OpenAtelierUI cannot write its default data directory at {data_dir}. "
        "Mount a writable directory there or set DATA_DIR/DATABASE_URL explicitly."
    )
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=data_dir,
            prefix=".openatelierui-write-test-",
        ):
            pass
    except OSError as exc:
        raise StartupPathError(message) from exc
    return data_dir


def _is_writable_directory(path: Path) -> bool:
    try:
        read_only_flag = getattr(os, "ST_RDONLY", 0)
        if read_only_flag and os.statvfs(path).f_flag & read_only_flag:
            return False
    except OSError:
        return False
    return os.access(path, os.W_OK)


def hydrate_frontend_static(frontend_build_dir: Path, static_dir: Path, logger) -> int:
    """Copy built static files when possible, without flooding read-only logs."""
    source_root = Path(frontend_build_dir) / "static"
    target_root = Path(static_dir)
    if not source_root.is_dir():
        return 0

    try:
        if source_root.resolve() == target_root.resolve():
            return 0
    except OSError:
        pass

    if target_root.exists() and not _is_writable_directory(target_root):
        logger.info(
            "Using pre-populated read-only STATIC_DIR at %s; asset hydration skipped",
            target_root,
        )
        return 0

    try:
        target_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "OpenAtelierUI could not prepare STATIC_DIR at %s; asset hydration skipped: %s",
            target_root,
            exc,
        )
        return 0

    copied = 0
    for source in source_root.glob("**/*"):
        if not source.is_file():
            continue
        destination = target_root / source.relative_to(source_root)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied += 1
        except OSError as exc:
            logger.warning(
                "OpenAtelierUI static asset hydration stopped at %s: %s",
                destination,
                exc,
            )
            break
    return copied
