# SPDX-License-Identifier: BSD-3-Clause
import contextlib, io, json, os, socket, sqlite3, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from validation import openatelierui_native as native
from validation import openatelierui_instance as shared


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.backend = Path(__file__).resolve().parents[1] / "backend"
        self.frontend = self.root / "frontend"
        (self.frontend / "static").mkdir(parents=True)
        (self.frontend / "index.html").write_text("synthetic")
        (self.frontend / "static" / "favicon.png").write_bytes(b"new")
        self.source = self.root / "source"
        self.source.mkdir()
        db = sqlite3.connect(self.source / "webui.db")
        db.executescript("CREATE TABLE auth(id TEXT PRIMARY KEY,email TEXT UNIQUE,password TEXT,active BOOLEAN);"
                         "CREATE TABLE user(id TEXT PRIMARY KEY,bio TEXT,gender TEXT,date_of_birth DATE);"
                         "INSERT INTO auth VALUES('synthetic-user','user@example.invalid','x',1);")
        db.commit(); db.close()
        (self.source / "uploads").mkdir()
        (self.source / "uploads" / "sample.txt").write_text("keep")
        self.source_before = shared._closed_data(self.source)
        self.venv = self.root / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "pyvenv.cfg").write_text("home = test\n")
        self.python = self.venv / "bin" / "python"
        self.python.symlink_to(sys.executable)
        self.envfile = self.root / "operator.env"
        self.secret = "synthetic-native-secret-keep-private"
        self.envfile.write_text("WEBUI_SECRET_KEY=" + self.secret + "\n")
        self.envfile.chmod(0o600)
        self.workspace = self.root / "workspace"
        self.args = SimpleNamespace(python=str(self.python), backend=str(self.backend), frontend=str(self.frontend),
                                    source_data=str(self.source), workspace=str(self.workspace), env_file=str(self.envfile), port=38171)

    def tearDown(self): self.tmp.cleanup()

    def test_prepares_private_copy_and_foreground_command_without_docker(self):
        calls, output = [], io.StringIO()
        original = subprocess.run
        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "-c" in command:
                return subprocess.CompletedProcess(command, 0)
            if "uvicorn" in command:
                return subprocess.CompletedProcess(command, 0)
            return original(command, **kwargs)
        with mock.patch.object(native, "_available"), mock.patch.object(native.subprocess, "run", side_effect=run), contextlib.redirect_stdout(output):
            self.assertEqual(native.start(self.args), 0)
        command, options = calls[-1]
        self.assertEqual(command[0], str(self.python.absolute()))
        self.assertIn("--host", command); self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[-1], "1"); self.assertEqual(options["cwd"], self.backend)
        env = options["env"]
        root = self.workspace.resolve()
        self.assertEqual(env["DATA_DIR"], str(root / "data")); self.assertEqual(env["HOST"], "127.0.0.1")
        self.assertEqual(env["ENABLE_SIGNUP"], "False"); self.assertEqual(env["OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS"], "1")
        self.assertEqual(env["CUSTOM_NAME"], ""); self.assertEqual(env["HOME"], str(root / "home"))
        self.assertNotIn("--forwarded-allow-ips", command); self.assertNotIn("docker", repr(calls).lower())
        report = json.loads(output.getvalue())
        self.assertEqual(report["url"], "http://127.0.0.1:38171"); self.assertNotIn(self.secret, output.getvalue())
        self.assertEqual(shared._closed_data(self.source), self.source_before)
        manifest = json.loads((self.workspace.resolve() / "native-instance.json").read_text())
        self.assertEqual(manifest["uploads_sha256"], shared._plain_tree(self.source / "uploads", shared.MAX_SOURCE_BYTES)["sha256"])
        self.assertEqual(manifest["pre_session_db_sha256"], shared._file_hash(self.workspace.resolve() / "pre-session" / "webui.db"))
        self.assertEqual((self.source / "uploads" / "sample.txt").read_text(), "keep")
        target = sqlite3.connect(self.workspace / "data" / "webui.db")
        self.assertIn("session_stamp", {row[1] for row in target.execute('PRAGMA table_info("auth")')})
        target.close()
        pre = sqlite3.connect(self.workspace / "pre-session" / "webui.db")
        self.assertNotIn("session_stamp", {row[1] for row in pre.execute('PRAGMA table_info("auth")')}); pre.close()
        self.assertEqual((root / "static" / "favicon.png").read_bytes(), b"new")
        self.assertEqual((root / "data" / "webui.db").stat().st_mode & 0o777, 0o600)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_unsafe_env_sidecar_overlap_and_busy_port_refuse_before_workspace(self):
        self.envfile.write_text("WEBUI_SECRET_KEY=" + self.secret + "\nOPENAI_API_KEY=do-not-print\n")
        self.envfile.chmod(0o600)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(native.main(["--python", str(self.python), "--backend", str(self.backend), "--frontend", str(self.frontend), "--source-data", str(self.source), "--workspace", str(self.workspace), "--env-file", str(self.envfile), "--port", "38171"]), 2)
        self.assertNotIn("do-not-print", out.getvalue()); self.assertFalse(self.workspace.exists())
        self.envfile.write_text("WEBUI_SECRET_KEY=" + self.secret + "\n"); self.envfile.chmod(0o600)
        Path(str(self.source / "webui.db") + "-wal").write_bytes(b"x")
        with self.assertRaises(shared.InstanceError): native.start(self.args)
        self.assertFalse(self.workspace.exists())
        Path(str(self.source / "webui.db") + "-wal").unlink()
        self.args.workspace = str(self.source / "nested")
        with mock.patch.object(native, "_available"):
            with self.assertRaises(shared.InstanceError): native.start(self.args)
        self.assertFalse((self.source / "nested").exists())
        self.args.workspace = str(self.workspace)
        source_link = self.root / "source-link"; source_link.symlink_to(self.source, target_is_directory=True)
        self.args.source_data = str(source_link)
        with mock.patch.object(native, "_available"):
            with self.assertRaises(shared.InstanceError): native.start(self.args)
        self.assertFalse(self.workspace.exists())
        self.args.source_data = str(self.source)
        with mock.patch.object(native, "_available", side_effect=shared.InstanceError("port_unavailable", "occupied")):
            with self.assertRaises(shared.InstanceError): native.start(self.args)
        self.assertFalse(self.workspace.exists())

    def test_port_probe_is_loopback_only_and_refuses_busy_port(self):
        class Busy:
            def bind(self, address): self.address = address; raise OSError("occupied")
            def close(self): self.closed = True
        with mock.patch.object(native.socket, "socket", return_value=Busy()) as factory:
            with self.assertRaises(shared.InstanceError): native._available(38171)
        self.assertEqual(factory.return_value.address, ("127.0.0.1", 38171)); self.assertTrue(factory.return_value.closed)

    def test_python_relative_path_keeps_venv_symlink(self):
        relative = os.path.relpath(self.python, Path.cwd())
        with mock.patch.object(native.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            self.assertEqual(native._python(relative), os.path.abspath(relative))


if __name__ == "__main__": unittest.main()
