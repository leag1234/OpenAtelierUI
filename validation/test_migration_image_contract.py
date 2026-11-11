# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Keep migration runtime inputs present in the distributable image."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MigrationImageContractTests(unittest.TestCase):
    def test_named_tool_defaults_are_packaged_beside_the_migrator(self):
        fixture = ROOT / "validation/fixtures/openatelier_v065_default_config.json"
        self.assertTrue(fixture.is_file())
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY --chown=$UID:$GID ./validation/fixtures "
            "/opt/openatelierui/migration/fixtures",
            dockerfile,
        )

    def test_restore_command_and_implementation_are_packaged_together(self):
        implementation = ROOT / "validation/restore_v0113_archive.py"
        wrapper = ROOT / "packaging/openatelierui-restore-v0113"
        self.assertTrue(implementation.is_file())
        self.assertTrue(wrapper.is_file())
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY --chown=$UID:$GID ./validation/restore_v0113_archive.py "
            "/opt/openatelierui/migration/restore_archive.py",
            dockerfile,
        )
        self.assertIn(
            "COPY --chown=$UID:$GID ./packaging/openatelierui-restore-v0113 "
            "/usr/local/bin/openatelierui-restore-v0113",
            dockerfile,
        )
        self.assertIn(
            "python3 -B /opt/openatelierui/migration/restore_archive.py",
            wrapper.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
