#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Prevent drift from the measured candidate 0.1 base images."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = (
    "FROM --platform=$BUILDPLATFORM "
    "node:22-alpine3.20@sha256:"
    "2289fb1fba0f4633b08ec47b94a89c7e20b829fc5679f9b7b298eaa2f1ed8b7e "
    "AS build",
    "FROM python:3.11-slim-bookworm@sha256:"
    "a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 "
    "AS base",
)


class BaseImageDigestTests(unittest.TestCase):
    def test_candidate_base_images_are_exactly_digest_pinned(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
        self.assertEqual(from_lines, list(EXPECTED))


if __name__ == "__main__":
    unittest.main(verbosity=2)
