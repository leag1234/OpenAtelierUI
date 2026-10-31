# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Opt-in foreground launcher for an isolated prebuilt instance."""
import argparse, json, os, shutil, socket, subprocess, sys
from pathlib import Path

from validation import openatelierui_instance as shared


def _overlap(left, right): return left == right or left in right.parents or right in left.parents


def _env_file(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o777 != 0o600 or path.stat().st_size > 4096:
        raise shared.InstanceError("invalid_env_file", "operator env file must be a regular mode-0600 file under 4 KiB")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key != "WEBUI_SECRET_KEY" or key in values or not value or any(ord(c) < 32 for c in value):
            raise shared.InstanceError("invalid_env_file", "env file accepts only one nonempty WEBUI_SECRET_KEY assignment")
        values[key] = value
    if not values: raise shared.InstanceError("missing_secret", "operator env file must provide WEBUI_SECRET_KEY")
    return values


def _python(path):
    path = Path(path)
    if not path.is_file() or not os.access(path, os.X_OK) or not (path.parent.parent / "pyvenv.cfg").is_file():
        raise shared.InstanceError("invalid_python", "provide an executable from an existing virtual environment")
    check = subprocess.run([str(path), "-c", "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)"], capture_output=True, timeout=10)
    if check.returncode:
        raise shared.InstanceError("unsupported_python", "virtual environment must use Python 3.11 or 3.12")
    return os.path.abspath(path)


def _available(port):
    if type(port) is not int or not 1 <= port <= 65535: raise shared.InstanceError("invalid_port", "port must be between 1 and 65535")
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        raise shared.InstanceError("port_unavailable", "requested loopback port is unavailable") from None
    finally:
        probe.close()


def start(args):
    py = _python(args.python)
    env_values = _env_file(args.env_file)
    backend, frontend = Path(args.backend), Path(args.frontend)
    source, workspace = Path(args.source_data), Path(args.workspace)
    for path, label in ((backend, "backend"), (frontend, "frontend")):
        if path.is_symlink() or not path.is_dir(): raise shared.InstanceError("invalid_" + label, label + " must be an existing directory")
    if not (backend / "open_webui" / "main.py").is_file() or not (frontend / "index.html").is_file() or not (frontend / "static").is_dir(): raise shared.InstanceError("incomplete_inputs", "backend entrypoint and compiled frontend are required")
    static_src = backend / "open_webui" / "static"
    for tree in (frontend, static_src): shared._plain_tree(tree, shared.MAX_STATIC_BYTES)
    source_hash = shared._closed_data(source)
    uploads_hash = shared._plain_tree(source / "uploads", shared.MAX_SOURCE_BYTES)["sha256"] if (source / "uploads").is_dir() else None
    profile = shared._profile_fields_enabled(source)
    if workspace.exists() or workspace.is_symlink(): raise shared.InstanceError("workspace_exists", "workspace must be a fresh path")
    parent = workspace.parent.resolve(strict=True)
    workspace = parent / workspace.name
    paths = [source.resolve(), backend.resolve(), frontend.resolve(), Path(args.env_file).resolve(), workspace]
    if any(_overlap(paths[i], paths[j]) for i in range(len(paths)) for j in range(i)): raise shared.InstanceError("overlapping_paths", "source, code, env and workspace paths must be separate")
    _available(args.port)

    workspace.mkdir(mode=0o700)
    os.chmod(workspace, 0o700)
    data = workspace / "data"
    shutil.copytree(source, data)
    shared._set_tree_modes(data, 0o700, 0o600)
    if shared._closed_data(source) != source_hash or shared._closed_data(data) != source_hash: raise shared.InstanceError("source_changed", "source or copied data changed during preparation")
    pre = workspace / "pre-session"
    pre.mkdir(mode=0o700)
    shutil.copyfile(data / "webui.db", pre / "webui.db")
    os.chmod(pre / "webui.db", 0o600)
    stage = workspace / ".epoch"
    stage.mkdir(mode=0o700)
    helper = backend / "open_webui" / "utils" / "session_epochs.py"
    result = subprocess.run([sys.executable, str(helper), "prepare-copy", "--source", str(pre / "webui.db"), "--target", str(stage / "webui.db")], capture_output=True, timeout=120)
    if result.returncode or not (stage / "webui.db").is_file(): raise shared.InstanceError("session_prepare_failed", "isolated session preparation failed; workspace retained")
    os.replace(stage / "webui.db", data / "webui.db")
    os.chmod(data / "webui.db", 0o600)
    stage.rmdir()
    cache = data / "cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    static = workspace / "static"
    shutil.copytree(static_src, static)
    shutil.copytree(frontend / "static", static, dirs_exist_ok=True)
    shared._set_tree_modes(static, 0o755, 0o644)

    home, tmp = workspace / "home", workspace / "tmp"
    home.mkdir(mode=0o700); tmp.mkdir(mode=0o700)
    runtime_env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL") if key in os.environ}
    runtime_env.update(env_values)
    runtime_env.update({"PYTHONPATH": str(backend.resolve()), "DATA_DIR": str(data), "CACHE_DIR": str(cache),
                        "STATIC_DIR": str(static), "FRONTEND_BUILD_DIR": str(frontend.resolve()),
                        "HOST": "127.0.0.1", "PORT": str(args.port), "WEBUI_AUTH": "True",
                        "ENABLE_SIGNUP": "False", "ENABLE_OAUTH_SIGNUP": "False", "CUSTOM_NAME": "",
                        "HOME": str(home), "TMPDIR": str(tmp), "XDG_CACHE_HOME": str(cache)})
    for key, value in shared.FLAGS.items():
        if key == "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS":
            value = "1" if profile else "0"
        runtime_env[key] = value
    if shared._closed_data(source) != source_hash: raise shared.InstanceError("source_changed", "source changed during preparation")
    report = {"status": "prepared", "url": f"http://127.0.0.1:{args.port}",
              "source_sha256": source_hash["sha256"], "source_files": source_hash["files"],
              "uploads_sha256": uploads_hash, "pre_session_db_sha256": shared._file_hash(pre / "webui.db"),
              "prepared_db_sha256": shared._file_hash(data / "webui.db"),
              "profile_fields_enabled": profile, "workspace": str(workspace)}
    raw = json.dumps(report, sort_keys=True).encode()
    manifest = workspace / "native-instance.json"
    manifest.write_bytes(raw)
    os.chmod(manifest, 0o600)
    print(json.dumps(report, sort_keys=True), flush=True)
    return subprocess.run([py, "-m", "uvicorn", "open_webui.main:app", "--host", "127.0.0.1",
                           "--port", str(args.port), "--workers", "1"], cwd=backend,
                          env=runtime_env, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Start one isolated OpenAtelierUI copy in the foreground.")
    for name in ("python", "backend", "frontend", "source-data", "workspace", "env-file"): parser.add_argument("--" + name, required=True)
    parser.add_argument("--port", type=int, required=True)
    try:
        return start(parser.parse_args(argv))
    except (shared.InstanceError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        code = getattr(error, "code", "native_start_refused")
        print(json.dumps({"status": "refused", "error_code": code, "detail": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
