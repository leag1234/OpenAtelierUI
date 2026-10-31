# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Mocked subprocess checks for the private OpenAtelierUI lifecycle helper."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import types
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest.mock import patch

from validation import openatelierui_instance as instance

SESSION_PATH = Path(__file__).resolve().parents[1] / "backend" / "open_webui" / "utils" / "session_epochs.py"
SESSION_SPEC = importlib.util.spec_from_file_location("candidate_session_epochs", SESSION_PATH)
session_epochs = importlib.util.module_from_spec(SESSION_SPEC)
SESSION_SPEC.loader.exec_module(session_epochs)

IMAGE = "sha256:" + "1" * 64
RUN_ID = "a" * 64
STATIC_ID = "b" * 64


class DockerDouble:
    def __init__(self, old_static, frontend):
        self.old_static, self.frontend = old_static, frontend
        self.calls = []
        self.container = None
        self.temp_name = None
        self.fail_inspect = False
        self.prep_modes = None

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        out = ""
        if args[:4] == ["docker", "image", "inspect", "--format"]:
            out = IMAGE
        elif args[:2] == ["docker", "create"]:
            self.temp_name = args[args.index("--name") + 1]
            out = STATIC_ID
        elif args[:2] == ["docker", "cp"]:
            source, destination = args[2], Path(args[3])
            tree = self.old_static if "/open_webui/static/." in source else self.frontend
            for root, dirs, files in os.walk(tree):
                rel = Path(root).relative_to(tree)
                (destination / rel).mkdir(parents=True, exist_ok=True)
                for name in files:
                    (destination / rel / name).write_bytes((Path(root) / name).read_bytes())
        elif args[:2] == ["docker", "rm"]:
            pass
        elif args[:3] == ["docker", "run", "--pull=never"] and "--rm" in args:
            source_mount = next(v for v in args if v.startswith("type=bind,src=") and ",dst=/source.db," in v)
            target_mount = next(v for v in args if v.startswith("type=bind,src=") and ",dst=/target" in v)
            source = Path(source_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            target = Path(target_mount.split("src=", 1)[1].split(",dst=", 1)[0]) / "webui.db"
            self.prep_modes = (source.stat().st_mode & 0o777, target.parent.stat().st_mode & 0o777)
            session_epochs.prepare_auth_session_stamp_copy(
                source, target)
        elif args[:4] == ["docker", "run", "--pull=never", "-d"]:
            out = RUN_ID
            labels = {args[i + 1].split("=", 1)[0]: args[i + 1].split("=", 1)[1]
                      for i, value in enumerate(args[:-1]) if value == "--label"}
            mounts = []
            for value in args:
                if value.startswith("type=bind,src=") and ",dst=" in value:
                    src, dst = value.split("src=", 1)[1].split(",dst=", 1)
                    mounts.append({"Source": src.split(",", 1)[0], "Destination": dst.split(",", 1)[0]})
            self.container = {"Id": RUN_ID, "Image": IMAGE, "Config": {"Labels": labels},
                              "Mounts": mounts, "State": {"Status": "running"}}
        elif args[:2] == ["docker", "inspect"]:
            if self.fail_inspect or self.container is None:
                return subprocess.CompletedProcess(args, 1, "", "")
            out = json.dumps([self.container])
        elif args[:2] == ["docker", "stop"]:
            self.container["State"]["Status"] = "exited"
        elif args[:2] == ["docker", "start"]:
            self.container["State"]["Status"] = "running"
        else:
            raise AssertionError(f"unexpected docker call prefix: {args[:4]}")
        return subprocess.CompletedProcess(args, 0, out, "")


class OpenAtelierUIInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source-data"
        self.source.mkdir()
        db = sqlite3.connect(self.source / "webui.db")
        db.execute("CREATE TABLE auth (id TEXT PRIMARY KEY, email TEXT, password TEXT, active INTEGER)")
        db.execute("INSERT INTO auth VALUES ('synthetic-user','user@example.invalid','x',1)")
        db.commit(); db.close()
        (self.source / "kept.txt").write_text("synthetic preserved file")
        self.env = self.root / "operator.env"
        self.env.write_text("WEBUI_SECRET_KEY=synthetic-secret-not-for-output\nOPENAI_API_BASE_URLS=https://provider.invalid\n")
        self.env.chmod(0o600)
        self.old_static, self.frontend = self.root / "old", self.root / "front"
        self.old_static.mkdir(); self.frontend.mkdir()
        (self.old_static / "shared.css").write_text("old")
        (self.old_static / "old.css").write_text("old-only")
        (self.frontend / "shared.css").write_text("frontend")
        (self.frontend / "app.js").write_text("frontend-only")
        self.docker = DockerDouble(self.old_static, self.frontend)
        self.args = types.SimpleNamespace(image="openatelier-test:local", expected_image_id=IMAGE,
            source_data=str(self.source), env_file=str(self.env), workspace=str(self.root / "private"),
            network="openatelierui-private", host_port=18080, container_port=8080, health_timeout=5)

    @contextmanager
    def _patch(self):
        with patch.object(instance.subprocess, "run", side_effect=self.docker), \
                patch.object(instance, "_health") as health, \
                patch.object(instance.os, "getuid", return_value=1000), \
                patch.object(instance.os, "getgid", return_value=1000):
            yield health

    def test_start_preserves_copy_merges_static_and_manages_exact_owned_container(self):
        original_db = (self.source / "webui.db").read_bytes()
        with self._patch():
            result = instance.start(self.args)
            self.assertEqual(result["url"], f"http://127.0.0.1:{self.args.host_port}")
            status = instance.operate(self.args.workspace, "status")
            self.assertEqual(status["status"], "running")
            self.assertEqual(status["url"], result["url"])
            self.assertEqual(instance.operate(self.args.workspace, "stop")["status"], "stopped")
            restarted = instance.operate(self.args.workspace, "restart")
            self.assertEqual(restarted["status"], "running")
            self.assertEqual(restarted["url"], result["url"])
        workspace = Path(self.args.workspace)
        manifest = json.loads((workspace / "instance.json").read_text())
        self.assertEqual(result["image_id"], IMAGE)
        self.assertFalse(result["profile_fields_enabled"])
        self.assertEqual((workspace / "pre-session" / "webui.db").read_bytes(), original_db)
        self.assertNotEqual((workspace / "data" / "webui.db").read_bytes(), original_db)
        check = sqlite3.connect(workspace / "data" / "webui.db")
        self.assertIn("session_stamp", {row[1] for row in check.execute('PRAGMA table_info("auth")')})
        check.close()
        self.assertEqual((workspace / "static" / "shared.css").read_text(), "frontend")
        self.assertEqual((workspace / "static" / "old.css").read_text(), "old-only")
        self.assertEqual((workspace / "static" / "app.js").read_text(), "frontend-only")
        self.assertEqual((workspace / "data" / "kept.txt").read_text(), "synthetic preserved file")
        self.assertEqual((workspace / "data" / "webui.db").stat().st_mode & 0o777, 0o600)
        self.assertEqual((workspace / "data" / "kept.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual((workspace / "data" / "cache").stat().st_mode & 0o777, 0o700)
        self.assertEqual((workspace / "pre-session").stat().st_mode & 0o777, 0o700)
        self.assertEqual((workspace / "pre-session" / "webui.db").stat().st_mode & 0o777, 0o600)
        self.assertEqual((workspace / "static").stat().st_mode & 0o777, 0o755)
        self.assertEqual((workspace / "static" / "shared.css").stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.docker.prep_modes, (0o600, 0o700))
        self.assertEqual(manifest["source_data"], instance._closed_data(self.source))
        self.assertEqual((workspace.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((workspace / "instance.json").stat().st_mode & 0o777), 0o600)
        self.assertNotIn("synthetic-secret-not-for-output", (workspace / "instance.json").read_text())
        self.assertTrue(any("--pull=never" in call for call in self.docker.calls))
        run = next(call for call in self.docker.calls if "-d" in call and call[:2] == ["docker", "run"])
        self.assertIn("127.0.0.1:18080:8080", run)
        self.assertIn("--read-only", run); self.assertIn("ALL", run); self.assertIn("2g", run)
        self.assertIn("--security-opt", run)
        self.assertIn("no-new-privileges", run)
        self.assertIn("--env-file", run)
        self.assertTrue(all(
            f"{key}={value}" in run
            for key, value in instance.FLAGS.items()
            if key != "OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS"
        ))
        self.assertIn("OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS=0", run)
        self.assertFalse(manifest["profile_fields_enabled"])
        self.assertIn("HOST=0.0.0.0", run); self.assertIn("PORT=8080", run)
        self.assertIn("--memory-swap", run); self.assertIn("nodev", run[run.index("--tmpfs") + 1])
        self.assertEqual(run[run.index("--memory-swap") + 1], "2g")
        self.assertEqual(run[run.index("--pids-limit") + 1], "256")
        self.assertEqual(run[run.index("--tmpfs") + 1], "/tmp:rw,noexec,nosuid,nodev,size=64m")
        self.assertTrue(any(value.startswith("openatelierui-instance-") for value in run))
        runtime_mounts = [run[i + 1] for i, value in enumerate(run[:-1]) if value == "--mount"]
        self.assertEqual(len(runtime_mounts), 2)
        self.assertTrue(all(mount.endswith((",dst=/app/backend/data", ",dst=/app/backend/open_webui/static"))
                            for mount in runtime_mounts))
        self.assertTrue(all("rw" not in mount.split(",") for mount in runtime_mounts))
        prep = next(call for call in self.docker.calls if "--rm" in call and call[:2] == ["docker", "run"])
        prep_mounts = [prep[i + 1] for i, value in enumerate(prep[:-1]) if value == "--mount"]
        self.assertIn("none", prep); self.assertIn("128", prep); self.assertIn("--cap-drop", prep)
        self.assertIn("--memory-swap", prep); self.assertIn("nodev", prep[prep.index("--tmpfs") + 1])
        self.assertEqual(prep[prep.index("--cpus") + 1], "2")
        self.assertEqual(prep[prep.index("--memory") + 1], "2g")
        self.assertEqual(prep[prep.index("--memory-swap") + 1], "2g")
        self.assertEqual(prep[prep.index("--pids-limit") + 1], "128")
        self.assertIn("--security-opt", prep)
        self.assertIn("no-new-privileges", prep)
        self.assertTrue(any(mount.endswith(",dst=/source.db,ro") for mount in prep_mounts))
        self.assertTrue(any(mount.endswith(",dst=/target") for mount in prep_mounts))
        self.assertTrue(all("rw" not in mount.split(",") for mount in prep_mounts))
        manifest.pop("profile_fields_enabled")
        (workspace / "instance.json").write_text(json.dumps(manifest), encoding="utf-8")
        with patch.object(instance.subprocess, "run", side_effect=self.docker), patch.object(instance, "_health"):
            self.assertEqual(instance.operate(self.args.workspace, "status")["status"], "running")
            self.assertEqual(instance.operate(self.args.workspace, "stop")["status"], "stopped")
            self.assertEqual(instance.operate(self.args.workspace, "restart")["status"], "running")

    def test_prepared_nullable_profile_schema_enables_only_its_existing_flag(self):
        db = sqlite3.connect(self.source / "webui.db")
        db.execute('CREATE TABLE "user" (id TEXT, bio TEXT, gender TEXT, date_of_birth DATE)')
        db.execute('INSERT INTO "user" VALUES (?,?,?,?)', ("synthetic-user", None, "", "2000-01-02"))
        db.commit(); db.close()
        original = instance._closed_data(self.source)
        with self._patch():
            result = instance.start(self.args)
        workspace = Path(self.args.workspace)
        manifest = json.loads((workspace / "instance.json").read_text())
        run = next(call for call in self.docker.calls if call[:2] == ["docker", "run"] and "-d" in call)
        self.assertTrue(manifest["profile_fields_enabled"])
        self.assertTrue(result["profile_fields_enabled"])
        self.assertIn("OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS=1", run)
        self.assertEqual(instance._closed_data(self.source), original)

    def test_partial_or_malformed_profile_schema_refuses_before_docker_or_workspace(self):
        schemas = (
            ('CREATE TABLE "user" (id TEXT, bio TEXT)', "partial"),
            ('CREATE TABLE "user" (id TEXT, bio BLOB, gender TEXT, date_of_birth DATE)', "wrong-type"),
            ('CREATE TABLE "user" (id TEXT, bio TEXT, gender TEXT NOT NULL, date_of_birth DATE)', "not-null"),
        )
        for ddl, name in schemas:
            with self.subTest(schema=name):
                source = self.root / ("source-" + name)
                shutil.copytree(self.source, source)
                with sqlite3.connect(source / "webui.db") as db:
                    db.execute(ddl)
                source_hash = instance._closed_data(source)
                args = types.SimpleNamespace(**vars(self.args))
                args.source_data = str(source)
                args.workspace = str(self.root / ("workspace-" + name))
                docker = DockerDouble(self.old_static, self.frontend)
                with patch.object(instance.subprocess, "run", side_effect=docker), \
                        patch.object(instance, "_health"), \
                        patch.object(instance.os, "getuid", return_value=1000), \
                        patch.object(instance.os, "getgid", return_value=1000):
                    with self.assertRaises(instance.InstanceError) as caught:
                        instance.start(args)
                self.assertEqual(caught.exception.code, "profile_schema_unsupported")
                self.assertEqual(docker.calls, [])
                self.assertFalse(Path(args.workspace).exists())
                self.assertEqual(instance._closed_data(source), source_hash)

    def test_closed_source_image_pin_and_owned_container_fail_closed(self):
        with patch.object(instance.subprocess, "run", side_effect=self.docker):
            with self.assertRaises(instance.InstanceError) as caught:
                instance._inspect_image(self.args.image, "sha256:" + "2" * 64)
        self.assertEqual(caught.exception.code, "image_identity_mismatch")
        (self.source / "webui.db-wal").write_bytes(b"sidecar")
        with self._patch():
            with self.assertRaises(instance.InstanceError) as caught:
                instance.start(self.args)
        self.assertEqual(caught.exception.code, "sqlite_sidecar")
        self.assertFalse(Path(self.args.workspace).exists())

    def test_wrong_container_label_is_never_stopped(self):
        with self._patch():
            instance.start(self.args)
        self.docker.container["Config"]["Labels"]["org.openatelier.instance"] = "other"
        with patch.object(instance.subprocess, "run", side_effect=self.docker):
            with self.assertRaises(instance.InstanceError):
                instance.operate(self.args.workspace, "stop")
        self.assertFalse(any(call[:2] == ["docker", "stop"] for call in self.docker.calls))

    def test_restart_health_failure_stops_only_verified_owned_container(self):
        with self._patch() as health:
            instance.start(self.args)
            health.side_effect = instance.InstanceError("health_timeout", "fixed")
            with self.assertRaises(instance.InstanceError):
                instance.operate(self.args.workspace, "restart")
        self.assertEqual(self.docker.container["State"]["Status"], "exited")
        self.assertTrue(any(call[:2] == ["docker", "stop"] and call[-1] == RUN_ID for call in self.docker.calls))
        manifest = json.loads((Path(self.args.workspace) / "instance.json").read_text())
        self.assertEqual(manifest["state"], "failed_stopped")

    def test_cli_shape_and_output_never_include_env_contents(self):
        argv = ["start", "--image", self.args.image, "--expected-image-id", IMAGE,
                "--source-data", self.args.source_data, "--env-file", str(self.env),
                "--workspace", self.args.workspace, "--network", self.args.network,
                "--host-port", str(self.args.host_port), "--container-port", str(self.args.container_port)]
        output = io.StringIO()
        with self._patch(), redirect_stdout(output):
            self.assertEqual(instance.main(argv), 0)
        self.assertNotIn("synthetic-secret-not-for-output", output.getvalue())
        self.assertIn('"status": "running"', output.getvalue())
        self.assertIn('"url": "http://127.0.0.1:18080"', output.getvalue())
        from validation import atlas_instance as legacy
        self.assertIs(legacy.main, instance.main)

    def test_symlinked_source_refused(self):
        alias = self.root / "alias-data"
        alias.symlink_to(self.source, target_is_directory=True)
        self.args.source_data = str(alias)
        with self._patch():
            with self.assertRaises(instance.InstanceError) as caught:
                instance.start(self.args)
        self.assertEqual(caught.exception.code, "unsafe_tree")
        self.assertFalse(Path(self.args.workspace).exists())

    def test_unsupported_operator_identity_and_env_permissions_refuse_before_artifacts(self):
        for uid, gid in ((1001, 1000), (1000, 1001)):
            with self.subTest(uid=uid, gid=gid), \
                    patch.object(instance.os, "getuid", return_value=uid), \
                    patch.object(instance.os, "getgid", return_value=gid), \
                    patch.object(instance.subprocess, "run", side_effect=self.docker):
                with self.assertRaises(instance.InstanceError) as caught: instance.start(self.args)
                self.assertEqual(caught.exception.code, "unsupported_operator_identity")
                self.assertFalse(Path(self.args.workspace).exists()); self.assertFalse(self.docker.calls)
        with self._patch():
            self.env.chmod(0o644)
            with self.assertRaises(instance.InstanceError) as caught: instance.start(self.args)
        self.assertEqual(caught.exception.code, "env_file_permissions")
        self.assertFalse(Path(self.args.workspace).exists())
        self.env.chmod(0o600)
        env_alias = self.root / "operator-env-alias"
        env_alias.symlink_to(self.env)
        self.args.env_file = str(env_alias)
        with self._patch():
            with self.assertRaises(instance.InstanceError) as caught: instance.start(self.args)
        self.assertEqual(caught.exception.code, "invalid_env_file")
        self.assertFalse(Path(self.args.workspace).exists())

    def test_health_uses_no_ambient_proxy(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class Opener:
            def open(self, url, timeout): self.url, self.timeout = url, timeout; return Response()
        opener = Opener()
        with patch.object(instance.urlrequest, "build_opener", return_value=opener) as build:
            instance._health(18080, 1)
        self.assertEqual(build.call_args.args[0].proxies, {})
        self.assertEqual(opener.url, "http://127.0.0.1:18080/health")


if __name__ == "__main__":
    unittest.main()
