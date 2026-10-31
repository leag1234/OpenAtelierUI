#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-isolated checks for the 2026-10-31 provenance remediation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProvenanceRemediationTests(unittest.TestCase):
    def test_inherited_services_are_not_default_runtime_targets(self):
        roots = [ROOT / "backend", ROOT / "src", ROOT / "kubernetes", ROOT / ".github"]
        files = [ROOT / "Dockerfile", ROOT / "docker-compose.yaml"]
        for root in roots:
            files.extend(path for path in root.rglob("*") if path.is_file())

        forbidden = (
            "api.openwebui.com",
            "openwebui.com",
            "open-webui/open-webui",
            "ghcr.io/open-webui",
            "docs.openwebui.com",
            "helm.openwebui.com",
        )
        violations = []
        for path in files:
            try:
                source = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for needle in forbidden:
                if needle in source:
                    violations.append(f"{path.relative_to(ROOT)}: {needle}")
        self.assertEqual(violations, [])

    def test_release_network_check_is_explicit_opt_in(self):
        source = (ROOT / "backend/open_webui/main.py").read_text(encoding="utf-8")
        self.assertIn('"OPENATELIERUI_ENABLE_RELEASE_CHECK", "false"', source)
        self.assertIn("if OFFLINE_MODE or not release_check_enabled:", source)

    def test_identity_and_local_image_names_are_project_specific(self):
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
        manifest = (ROOT / "kubernetes/manifest/base/webui-deployment.yaml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(package["name"], "openatelierui")
        self.assertIn('name = "openatelierui"', pyproject)
        self.assertIn('"License :: OSI Approved :: BSD License"', pyproject)
        self.assertNotIn('"License :: OSI Approved :: MIT License"', pyproject)
        self.assertIn('openatelierui = "open_webui:app"', pyproject)
        self.assertIn('open-webui = "open_webui:app"', pyproject)
        self.assertIn("image: openatelierui:", compose)
        self.assertIn("pull_policy: never", compose)
        self.assertIn("image: openatelierui:local", manifest)
        self.assertIn("imagePullPolicy: Never", manifest)

    def test_container_metadata_and_notices_are_project_specific(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn('org.opencontainers.image.title="OpenAtelierUI"', dockerfile)
        self.assertIn(
            'org.opencontainers.image.source="https://github.com/leag1234/OpenAtelierUI"',
            dockerfile,
        )
        self.assertIn('org.opencontainers.image.licenses="BSD-3-Clause"', dockerfile)
        self.assertIn(
            "COPY --chown=$UID:$GID ./LICENSE ./NOTICE ./PROVENANCE.md /opt/openatelierui/",
            dockerfile,
        )

    def test_upstream_community_export_flows_are_absent(self):
        component_root = ROOT / "src/lib/components"
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in component_root.rglob("*.svelte")
        )
        self.assertNotIn("Share to Open WebUI Community", source)
        self.assertNotIn("Redirecting you to Open WebUI Community", source)
        self.assertNotIn("Made by Open WebUI Community", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
