# SPDX-License-Identifier: BSD-3-Clause
from __future__ import annotations

import argparse, hashlib, json, os, re, shutil, sqlite3, subprocess, tempfile, time, uuid
from pathlib import Path
from urllib import error as urlerror, request as urlrequest

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_STATIC_BYTES = 128 * 1024 * 1024
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}\Z")
FLAGS = {
    "OPENATELIER_SESSION_EPOCHS": "1",
    "OPENATELIER_PRIVATE_NOTES_COMPAT": "1",
    "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS": "1",
    "OPEN_WEBUI_IMPORTED_HISTORY_READS": "1",
    "OPENATELIER_LAZY_LOCAL_EMBEDDING_STARTUP": "1",
    "ENABLE_SIGNUP": "False",
    "ENABLE_OAUTH_SIGNUP": "False",
    "ENABLE_TITLE_GENERATION": "False",
    "ENABLE_TAGS_GENERATION": "False",
    "ENABLE_AUTOCOMPLETE_GENERATION": "False",
    "ENABLE_VERSION_UPDATE_CHECK": "False",
    "ENABLE_FOLLOW_UP_GENERATION": "False",
    "RAG_EMBEDDING_MODEL_AUTO_UPDATE": "False",
    "RAG_RERANKING_MODEL_AUTO_UPDATE": "False",
    "WHISPER_MODEL_AUTO_UPDATE": "False",
}


class InstanceError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _safe_tag(value):
    if not isinstance(value, str) or not TAG_RE.fullmatch(value) or "@" in value: raise InstanceError("invalid_image_tag", "provide an existing local image tag")
    return value


def _safe_image_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}\Z", value): raise InstanceError("invalid_image_id", "expected a sha256 image config ID")
    return value


def _plain_tree(root, limit):
    root = Path(root)
    if root.is_symlink() or not root.is_dir(): raise InstanceError("unsafe_tree", "input must be a regular directory without symlinks")
    total = count = 0
    digest = hashlib.sha256()
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        base = Path(current)
        for name in dirs + files:
            path = base / name
            if path.is_symlink():
                raise InstanceError("unsafe_tree", "input tree contains a symlink")
        for name in files:
            path = base / name
            if not path.is_file():
                raise InstanceError("unsafe_tree", "input tree contains a non-regular file")
            count += 1
            if count > 8192:
                raise InstanceError("tree_too_large", "input contains too many files")
            size = path.stat().st_size
            total += size
            if total > limit:
                raise InstanceError("tree_too_large", "input exceeds the bounded copy size")
            rel = path.relative_to(root).as_posix().encode()
            file_hash = _file_hash(path)
            digest.update(len(rel).to_bytes(4, "big") + rel + size.to_bytes(8, "big") + bytes.fromhex(file_hash))
    return {"sha256": digest.hexdigest(), "bytes": total, "files": count}


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def _set_tree_modes(root, directory_mode, file_mode):
    for current, dirs, files in os.walk(root):
        os.chmod(current, directory_mode)
        for name in files: os.chmod(Path(current) / name, file_mode)


def _closed_data(path):
    root = Path(path)
    profile = _plain_tree(root, MAX_SOURCE_BYTES)
    db = root / "webui.db"
    if db.is_symlink() or not db.is_file(): raise InstanceError("missing_database", "source data must contain a regular webui.db")
    if any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise InstanceError("sqlite_sidecar", "source database is not closed")
    return profile


def _profile_fields_enabled(path):
    database = (Path(path) / "webui.db").resolve(strict=True)
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        columns = {row[1]: row for row in connection.execute('PRAGMA table_info("user")')}
    except sqlite3.DatabaseError:
        raise InstanceError("profile_schema_unsupported", "source user schema cannot be inspected") from None
    finally:
        if connection is not None:
            connection.close()
    expected = {"bio": "TEXT", "gender": "TEXT", "date_of_birth": "DATE"}
    present = set(expected) & set(columns)
    if not present:
        return False
    if present != set(expected) or any(
        str(columns[name][2]).upper() != kind or columns[name][3] != 0
        for name, kind in expected.items()
    ):
        raise InstanceError("profile_schema_unsupported", "source profile fields are partial or not nullable supported types")
    return True


def _run(args, *, input_text=None, timeout=30):
    try:
        result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstanceError("docker_unavailable", "Docker command failed or exceeded its bound") from error
    if result.returncode:
        raise InstanceError("docker_operation_failed", "Docker refused the bounded lifecycle operation")
    return result.stdout.strip()


def _inspect_image(tag, expected):
    actual = _run(["docker", "image", "inspect", "--format", "{{.Id}}", tag])
    if actual != expected: raise InstanceError("image_identity_mismatch", "local image tag does not match the expected config ID")
    return actual


def _copy_image_static(image_id, stage, instance_id):
    name = "openatelierui-static-" + instance_id
    cid = ""
    try:
        cid = _run(["docker", "create", "--pull=never", "--name", name, "--network", "none",
                    "--read-only", "--user", "1000:1000", "--entrypoint", "/bin/true", image_id])
        if not re.fullmatch(r"[0-9a-f]{12,64}", cid):
            raise InstanceError("invalid_container_id", "Docker returned an invalid temporary container identity")
        old = stage / "old-static"; build = stage / "build-static"
        old.mkdir(); build.mkdir()
        _run(["docker", "cp", cid + ":/app/backend/open_webui/static/.", str(old)])
        _run(["docker", "cp", cid + ":/app/build/static/.", str(build)])
        _plain_tree(old, MAX_STATIC_BYTES); _plain_tree(build, MAX_STATIC_BYTES)
        merged = stage / "static"
        shutil.copytree(old, merged)
        shutil.copytree(build, merged, dirs_exist_ok=True)
        summary = _plain_tree(merged, MAX_STATIC_BYTES)
        return summary, _plain_tree(old, MAX_STATIC_BYTES), _plain_tree(build, MAX_STATIC_BYTES)
    finally:
        if cid: _run(["docker", "rm", cid], timeout=30)


def _manifest_path(workspace):
    p = Path(workspace)
    if p.is_symlink() or not p.is_dir():
        raise InstanceError("invalid_workspace", "workspace must be a private existing directory")
    if p.stat().st_mode & 0o777 != 0o700: raise InstanceError("workspace_permissions", "workspace must remain mode 0700")
    manifest = p / "instance.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise InstanceError("manifest_missing", "workspace has no lifecycle manifest")
    if manifest.stat().st_size > 32768:
        raise InstanceError("manifest_invalid", "lifecycle manifest exceeds its bound")
    if manifest.stat().st_mode & 0o777 != 0o600: raise InstanceError("manifest_permissions", "lifecycle manifest must remain mode 0600")
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise InstanceError("manifest_invalid", "lifecycle manifest is invalid")
    required = {"workspace_hash", "image_id", "image_tag", "instance_id", "container_name", "container_id", "data_path", "static_path", "network", "host_port", "health_timeout"}
    if (not isinstance(value, dict) or value.get("schema") != 1 or not required.issubset(value) or value.get("workspace_hash") != hashlib.sha256(str(p.resolve()).encode()).hexdigest() or value.get("data_path") != str(p.resolve() / "data") or value.get("static_path") != str(p.resolve() / "static")):
        raise InstanceError("manifest_invalid", "lifecycle manifest is unsupported for this workspace")
    return p.resolve(), value


def _save_manifest(workspace, value):
    path = Path(workspace) / "instance.json"
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 32768:
        raise InstanceError("manifest_too_large", "lifecycle manifest exceeds its bound")
    fd, temp = tempfile.mkstemp(prefix=".manifest-", dir=workspace)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path); os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def _container(manifest):
    ref = manifest.get("container_id") or manifest.get("container_name")
    if not isinstance(ref, str) or not ref:
        raise InstanceError("container_identity_missing", "manifest has no owned container identity")
    try:
        data = json.loads(_run(["docker", "inspect", ref]))[0]
    except (ValueError, IndexError, KeyError, TypeError):
        raise InstanceError("container_missing", "owned container is unavailable")
    labels = data.get("Config", {}).get("Labels") or {}
    expected_label = manifest.get("instance_id")
    if manifest.get("container_id") and data.get("Id") != manifest["container_id"]:
        raise InstanceError("container_identity_mismatch", "container ID differs from the private lifecycle manifest")
    if (data.get("Image") != manifest.get("image_id") or labels.get("org.openatelier.instance") != expected_label
            or labels.get("org.openatelier.workspace") != manifest.get("workspace_hash")):
        raise InstanceError("container_identity_mismatch", "container does not match the private lifecycle manifest")
    mounts = {(m.get("Destination"), os.path.realpath(m.get("Source", ""))) for m in data.get("Mounts", [])}
    for source, destination in ((manifest["data_path"], "/app/backend/data"),
                                (manifest["static_path"], "/app/backend/open_webui/static")):
        if (destination, os.path.realpath(source)) not in mounts:
            raise InstanceError("container_mount_mismatch", "container mounts differ from the prepared workspace")
    return data


def _health(host_port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlrequest.build_opener(urlrequest.ProxyHandler({})).open(f"http://127.0.0.1:{host_port}/health", timeout=2) as response:
                if response.status == 200: return
        except (OSError, urlerror.URLError):
            time.sleep(1)
    raise InstanceError("health_timeout", "application did not become healthy within the bounded wait")


def _stop_failed(workspace, manifest):
    try:
        owned = _container(manifest)
        if owned.get("State", {}).get("Status") == "running": _run(["docker", "stop", "--time", "10", owned["Id"]], timeout=20)
        manifest.update(container_id=owned.get("Id"), state="failed_stopped"); _save_manifest(workspace, manifest)
    except Exception: manifest["state"] = "stop_not_confirmed"; _save_manifest(workspace, manifest)


def start(args):
    if not hasattr(os, "getuid") or not hasattr(os, "getgid") or os.getuid() != 1000 or os.getgid() != 1000:
        raise InstanceError("unsupported_operator_identity", "run lifecycle preparation as UID/GID 1000")
    tag = _safe_tag(args.image); expected = _safe_image_id(args.expected_image_id)
    if not 1 <= args.container_port <= 65535 or not 1 <= args.host_port <= 65535: raise InstanceError("invalid_port", "host and container ports must be between 1 and 65535")
    if args.health_timeout < 1 or args.health_timeout > 240: raise InstanceError("invalid_health_timeout", "health timeout must be between 1 and 240 seconds")
    if not args.network or args.network == "none" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.network): raise InstanceError("invalid_network", "select an existing named runtime network")
    env = Path(args.env_file)
    if env.is_symlink() or not env.is_file():
        raise InstanceError("invalid_env_file", "explicit env file must be a regular non-symlink file")
    if env.stat().st_mode & 0o777 != 0o600:
        raise InstanceError("env_file_permissions", "operator env file must have mode 0600")
    data_src = Path(args.source_data)
    source_summary = _closed_data(data_src)
    profile_fields_enabled = _profile_fields_enabled(data_src)
    if data_src.resolve() == Path(args.workspace).resolve() or "," in str(Path(args.workspace).absolute()):
        raise InstanceError("workspace_is_source", "workspace must be separate and use a bind-mount-safe path")
    image_id = _inspect_image(tag, expected)
    workspace = Path(args.workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(mode=0o700)
    os.chmod(workspace, 0o700)
    instance_id = uuid.uuid4().hex
    cname = "openatelierui-instance-" + instance_id[:16]
    manifest = {"schema": 1, "instance_id": instance_id, "container_name": cname,
                "container_id": None, "image_tag": tag, "image_id": image_id,
                "workspace_hash": hashlib.sha256(str(workspace.resolve()).encode()).hexdigest(),
                "data_path": str((workspace / "data").resolve()),
                "static_path": str((workspace / "static").resolve()),
                "env_file": str(env.resolve()), "network": args.network,
                "host_port": args.host_port, "container_port": args.container_port,
                "state": "preparing", "source_data": source_summary,
                "profile_fields_enabled": profile_fields_enabled}
    _save_manifest(workspace, manifest)
    try:
        shutil.copytree(data_src, workspace / "data")
        if _closed_data(data_src) != source_summary or _closed_data(workspace / "data") != source_summary:
            raise InstanceError("source_changed", "source data changed while the copy was prepared")
        cache = workspace / "data" / "cache"
        cache.mkdir(mode=0o700, exist_ok=True)
        _set_tree_modes(workspace / "data", 0o700, 0o600)
        (workspace / "pre-session").mkdir(mode=0o700)
        shutil.copy2(workspace / "data" / "webui.db", workspace / "pre-session" / "webui.db")
        stage = workspace / ".static-stage"; stage.mkdir(mode=0o700)
        static_summary, old_summary, build_summary = _copy_image_static(image_id, stage, instance_id)
        os.rename(stage / "static", workspace / "static")
        shutil.rmtree(stage)
        _set_tree_modes(workspace / "static", 0o755, 0o644)
        prep_temp = workspace / ".prepared"; prep_temp.mkdir(mode=0o700)
        _run(["docker", "run", "--pull=never", "--rm", "--network", "none", "--read-only",
              "--user", "1000:1000", "--cpus", "2", "--memory", "2g", "--memory-swap", "2g",
              "--pids-limit", "128", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
              "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
              "--mount", f"type=bind,src={workspace / 'pre-session' / 'webui.db'},dst=/source.db,ro",
              "--mount", f"type=bind,src={prep_temp},dst=/target",
              "--entrypoint", "python", image_id,
              "/app/backend/open_webui/utils/session_epochs.py", "prepare-copy",
              "--source", "/source.db", "--target", "/target/webui.db"], timeout=120)
        prepared_db = prep_temp / "webui.db"
        if not prepared_db.is_file() or prepared_db.is_symlink():
            raise InstanceError("epoch_copy_missing", "session preparation did not produce its isolated database")
        os.replace(prepared_db, workspace / "data" / "webui.db"); os.chmod(workspace / "data" / "webui.db", 0o600)
        shutil.rmtree(prep_temp)
        manifest.update(state="prepared", session_database_sha256=_file_hash(workspace / "data" / "webui.db"),
                        pre_session_sha256=_file_hash(workspace / "pre-session" / "webui.db"),
                        static=static_summary, static_base=old_summary, static_frontend=build_summary,
                        health_timeout=args.health_timeout)
        _save_manifest(workspace, manifest)
        command = ["docker", "run", "--pull=never", "-d", "--name", cname,
                   "--label", "org.openatelier.instance=" + instance_id,
                   "--label", "org.openatelier.workspace=" + manifest["workspace_hash"],
                   "--network", args.network, "--read-only", "--user", "1000:1000",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--pids-limit", "256", "--cpus", "2", "--memory", "2g", "--memory-swap", "2g",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
                   "--publish", f"127.0.0.1:{args.host_port}:{args.container_port}",
                   "--mount", f"type=bind,src={manifest['data_path']},dst=/app/backend/data",
                   "--mount", f"type=bind,src={manifest['static_path']},dst=/app/backend/open_webui/static",
                   "--env-file", manifest["env_file"]]
        for key, value in FLAGS.items():
            if key == "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS":
                value = "1" if profile_fields_enabled else "0"
            command.extend(["--env", f"{key}={value}"])
        command.extend(["--env", "HOST=0.0.0.0", "--env", f"PORT={args.container_port}"])
        command.extend([image_id])
        cid = _run(command, timeout=60)
        if not re.fullmatch(r"[0-9a-f]{12,64}", cid):
            raise InstanceError("invalid_container_id", "Docker returned an invalid container identity")
        manifest.update(container_id=cid, state="starting")
        _save_manifest(workspace, manifest)
        _container(manifest)
        _health(args.host_port, args.health_timeout)
        manifest["state"] = "running"; _save_manifest(workspace, manifest)
        return {"status": "running", "workspace": str(workspace.resolve()), "image_id": image_id,
                "profile_fields_enabled": profile_fields_enabled}
    except Exception: _stop_failed(workspace, manifest); raise


def operate(workspace, action):
    root, manifest = _manifest_path(workspace)
    data = _container(manifest)
    state = data.get("State", {}).get("Status", "unknown")
    cid = data.get("Id")
    if action == "status": return {"status": state, "image_id": manifest["image_id"], "workspace": str(root)}
    if action == "stop":
        if state == "running": _run(["docker", "stop", "--time", "10", cid], timeout=20)
        manifest["state"] = "stopped"; _save_manifest(root, manifest)
        return {"status": "stopped", "workspace": str(root)}
    if action == "restart":
        if state == "running": _run(["docker", "stop", "--time", "10", cid], timeout=20)
        try:
            _run(["docker", "start", cid], timeout=30)
            _health(manifest["host_port"], manifest.get("health_timeout", 240))
        except Exception: _stop_failed(root, manifest); raise
        manifest["state"] = "running"; _save_manifest(root, manifest)
        return {"status": "running", "workspace": str(root)}
    raise InstanceError("unknown_action", "unsupported lifecycle action")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare and manage a private OpenAtelierUI instance copy.")
    sub = parser.add_subparsers(dest="action", required=True)
    begin = sub.add_parser("start")
    for name in ("image", "expected-image-id", "source-data", "env-file", "workspace", "network"):
        begin.add_argument("--" + name, required=True)
    begin.add_argument("--host-port", type=int, required=True)
    begin.add_argument("--container-port", type=int, required=True)
    begin.add_argument("--health-timeout", type=int, default=240)
    for action in ("status", "stop", "restart"): item = sub.add_parser(action); item.add_argument("--workspace", required=True)
    args = parser.parse_args(argv)
    try:
        result = start(args) if args.action == "start" else operate(args.workspace, args.action)
        print(json.dumps(result, sort_keys=True)); return 0
    except (InstanceError, OSError) as error:
        code = error.code if isinstance(error, InstanceError) else "filesystem_operation_failed"
        detail = str(error) if isinstance(error, InstanceError) else "workspace operation could not be completed"
        print(json.dumps({"status": "refused", "error_code": code, "detail": detail}, sort_keys=True)); return 2


if __name__ == "__main__": raise SystemExit(main())
