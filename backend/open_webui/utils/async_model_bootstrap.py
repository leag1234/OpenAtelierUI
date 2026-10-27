# SPDX-License-Identifier: BSD-3-Clause
"""Explicit hash-bound launcher for one isolated async model bootstrap."""

import asyncio
import hashlib
import hmac
import importlib
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar


MAX_BOOTSTRAP_BYTES = 1024 * 1024
_ACTIVE_SCOPE = ContextVar("openatelier_async_model_bootstrap_active", default=False)
_FUNCTION_METHODS = frozenset((
    "get_function_by_id", "insert_new_function", "update_function_by_id"
))
_MODEL_METHODS = frozenset((
    "get_model_by_id", "insert_new_model", "update_model_by_id"
))


class BootstrapRefused(ValueError):
    """A selected bootstrap did not satisfy the local hash-bound contract."""


class BootstrapUnavailable(RuntimeError):
    """A selected bootstrap process could not be started or completed."""


class _AsyncModelProxy:
    def __init__(self, target, methods):
        self._target = target
        self._methods = methods

    def __getattr__(self, name):
        method = getattr(self._target, name)
        if name not in self._methods:
            return method

        async def call(*args, **kwargs):
            return await asyncio.to_thread(method, *args, **kwargs)

        return call


@contextmanager
def _model_api_scope(functions_module, models_module):
    """Temporarily proxy only the six measured async model entry points."""
    if _ACTIVE_SCOPE.get():
        raise BootstrapRefused("An async model bootstrap scope is already active")
    if not hasattr(functions_module, "Functions") or not hasattr(models_module, "Models"):
        raise BootstrapRefused("Required model APIs are unavailable")
    active_token = _ACTIVE_SCOPE.set(True)
    original_functions = functions_module.Functions
    original_models = models_module.Models
    try:
        functions_module.Functions = _AsyncModelProxy(original_functions, _FUNCTION_METHODS)
        models_module.Models = _AsyncModelProxy(original_models, _MODEL_METHODS)
        yield
    finally:
        functions_module.Functions = original_functions
        models_module.Models = original_models
        _ACTIVE_SCOPE.reset(active_token)


def _read_verified_script(script_path, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise BootstrapRefused("An explicit SHA-256 digest is required")
    try:
        path = os.fspath(script_path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BOOTSTRAP_BYTES:
                raise BootstrapRefused("The selected bootstrap file is unsupported")
            parts, remaining = [], MAX_BOOTSTRAP_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                parts.append(chunk)
                remaining -= len(chunk)
            source = b"".join(parts)
            if len(source) > MAX_BOOTSTRAP_BYTES:
                raise BootstrapRefused("The selected bootstrap file is oversized")
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise BootstrapRefused("The selected bootstrap file is unsupported")
        finally:
            os.close(descriptor)
    except BootstrapRefused:
        raise
    except (OSError, TypeError, ValueError):
        raise BootstrapRefused("The selected bootstrap file is unavailable") from None
    digest = hashlib.sha256(source).hexdigest()
    if not hmac.compare_digest(digest, expected_sha256.lower()):
        raise BootstrapRefused("The selected bootstrap digest does not match")
    return source


_CHILD_ENTRY = (
    "from open_webui.utils.async_model_bootstrap import _child_main\n"
    "raise SystemExit(_child_main())\n"
)


def run_verified_bootstrap(script_path, expected_sha256, args=()):
    """Run exact verified script bytes in a child process with scoped proxies.

    This is an explicit code-execution helper, not a sandbox. The caller must
    choose the script and its digest; report/config values are never executed.
    Child output is discarded so script content cannot enter helper errors.
    """
    source = _read_verified_script(script_path, expected_sha256)
    if not isinstance(args, (tuple, list)) or any(
        not isinstance(arg, str) or "\x00" in arg for arg in args
    ):
        raise BootstrapRefused("Bootstrap arguments are unsupported")
    try:
        result = subprocess.run(
            [sys.executable, "-c", _CHILD_ENTRY, os.fspath(script_path),
             expected_sha256.lower(), *args],
            input=source,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, ValueError):
        raise BootstrapUnavailable("The selected bootstrap could not be started") from None
    return result.returncode


def _child_main():
    if len(sys.argv) < 3:
        return 2
    script_path, expected_sha256, *script_args = sys.argv[1:]
    source = sys.stdin.buffer.read(MAX_BOOTSTRAP_BYTES + 1)
    if len(source) > MAX_BOOTSTRAP_BYTES or not hmac.compare_digest(
        hashlib.sha256(source).hexdigest(), expected_sha256
    ):
        return 2
    previous_argv = sys.argv
    try:
        # Import application configuration before the external script can
        # start its own event loop, preserving the old import lifecycle.
        importlib.import_module("open_webui.config")
        functions_module = importlib.import_module("open_webui.models.functions")
        models_module = importlib.import_module("open_webui.models.models")
        sys.argv = [script_path, *script_args]
        namespace = {"__name__": "__main__", "__file__": script_path, "__package__": None}
        with _model_api_scope(functions_module, models_module):
            exec(compile(source, script_path, "exec"), namespace)
        return 0
    except SystemExit as error:
        code = error.code
        return code if type(code) is int and 0 <= code <= 255 else 1
    except BaseException:
        return 1
    finally:
        sys.argv = previous_argv
