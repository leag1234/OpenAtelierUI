# Copyright (c) 2026 Gaël Duval
# SPDX-License-Identifier: BSD-3-Clause

import importlib.util
import logging
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "backend" / "open_webui" / "utils" / "startup_paths.py"
SPEC = importlib.util.spec_from_file_location("startup_paths_under_test", MODULE_PATH)
startup_paths = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(startup_paths)


class StartupHardeningTests(unittest.TestCase):
    def test_default_database_url_remains_under_data_dir(self):
        source = (ROOT / "backend" / "open_webui" / "env.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/webui.db")',
            source,
        )

    def test_startup_banner_uses_openatelierui_identity(self):
        source = (ROOT / "backend" / "open_webui" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("OpenAtelierUI v{VERSION}", source)
        self.assertIn("https://github.com/leag1234/OpenAtelierUI", source)
        self.assertNotIn("https://github.com/open-webui/open-webui", source)

    def test_start_script_forwards_extra_arguments_as_separate_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            capture = root / "argv.txt"
            fake = fake_bin / "uvicorn"
            fake.write_text(
                "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$OPENATELIERUI_ARGV_CAPTURE\"\n",
                encoding="utf-8",
            )
            fake.chmod(0o700)
            environment = os.environ.copy()
            environment.update(
                PATH=f"{fake_bin}{os.pathsep}{environment['PATH']}",
                WEBUI_SECRET_KEY="synthetic-test-key",
                OPENATELIERUI_ARGV_CAPTURE=str(capture),
            )
            completed = subprocess.run(
                [
                    "bash",
                    str(ROOT / "backend" / "start.sh"),
                    "--log-level",
                    "warning",
                    "--timeout-keep-alive",
                    "7",
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            arguments = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                arguments[-4:],
                ["--log-level", "warning", "--timeout-keep-alive", "7"],
            )

    def test_implicit_data_directory_has_actionable_read_only_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "data"
            with mock.patch.object(
                startup_paths.tempfile,
                "NamedTemporaryFile",
                side_effect=PermissionError("read-only filesystem"),
            ):
                with self.assertRaises(startup_paths.StartupPathError) as raised:
                    startup_paths.ensure_implicit_data_directory(path)
            message = str(raised.exception)
            self.assertIn("OpenAtelierUI", message)
            self.assertIn("DATA_DIR/DATABASE_URL", message)
            self.assertIn(str(path), message)

    def test_implicit_data_directory_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "data"
            self.assertEqual(
                startup_paths.ensure_implicit_data_directory(path), path
            )
            self.assertTrue(path.is_dir())

    def test_writable_static_tree_is_hydrated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "frontend"
            (source / "static" / "nested").mkdir(parents=True)
            (source / "static" / "nested" / "asset.txt").write_text(
                "asset", encoding="utf-8"
            )
            target = root / "served-static"
            copied = startup_paths.hydrate_frontend_static(
                source, target, logging.getLogger("startup-hardening-test")
            )
            self.assertEqual(copied, 1)
            self.assertEqual(
                (target / "nested" / "asset.txt").read_text(encoding="utf-8"),
                "asset",
            )

    def test_prepopulated_read_only_static_tree_skips_copy_without_error_spam(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "frontend"
            (source / "static").mkdir(parents=True)
            (source / "static" / "asset.txt").write_text("new", encoding="utf-8")
            target = root / "served-static"
            target.mkdir()
            (target / "asset.txt").write_text("existing", encoding="utf-8")
            logger = mock.Mock()
            with mock.patch.object(startup_paths.os, "access", return_value=False):
                with mock.patch.object(
                    startup_paths.shutil,
                    "copyfile",
                    side_effect=AssertionError("copy must not run"),
                ):
                    copied = startup_paths.hydrate_frontend_static(
                        source, target, logger
                    )
            self.assertEqual(copied, 0)
            logger.info.assert_called_once()
            logger.warning.assert_not_called()
            self.assertEqual(
                (target / "asset.txt").read_text(encoding="utf-8"), "existing"
            )


if __name__ == "__main__":
    unittest.main()
