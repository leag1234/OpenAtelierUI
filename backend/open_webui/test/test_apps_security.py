# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused security tests for the BSD Apps compatibility primitives."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from open_webui.models.apps import Manifest, _decrypt, _encrypt
from open_webui.utils.apps_runtime import AppProtocolError, validate_app_url


def manifest(public_key: str) -> dict:
    return {
        "id": "community-workshop-app",
        "name": "Community Workshop App",
        "description": "Synthetic isolated app",
        "public_key": public_key,
        "requested_scopes": ["channels:read"],
    }


class AppsSecurityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.public_key = (
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )

    def test_secret_cipher_does_not_retain_plaintext(self):
        plaintext = "synthetic-app-secret-never-for-production"
        encrypted = _encrypt(plaintext)
        self.assertIsInstance(encrypted, str)
        self.assertNotIn(plaintext, encrypted)
        self.assertEqual(_decrypt(encrypted), plaintext)

    def test_manifest_pins_ed25519_key_and_rejects_unknown_fields(self):
        parsed = Manifest.model_validate(manifest(self.public_key.rstrip()))
        self.assertEqual(parsed.public_key, self.public_key)
        invalid = manifest(self.public_key)
        invalid["unexpected"] = True
        with self.assertRaises(ValidationError):
            Manifest.model_validate(invalid)

    async def test_private_http_requires_exact_explicit_origin(self):
        with patch.dict(
            os.environ,
            {"APP_ALLOWED_PRIVATE_ORIGINS": "http://round57-recorder:8081"},
            clear=False,
        ):
            parsed, origin = await validate_app_url("http://round57-recorder:8081")
            self.assertEqual(parsed.hostname, "round57-recorder")
            self.assertEqual(origin, "http://round57-recorder:8081")
            with self.assertRaises(AppProtocolError):
                await validate_app_url("http://round57-recorder:8082")

    async def test_credentials_query_and_fragment_are_rejected(self):
        invalid_urls = (
            "https://user:password@example.test",
            "https://example.test?token=secret",
            "https://example.test#fragment",
            "file:///tmp/manifest.json",
        )
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(AppProtocolError):
                await validate_app_url(url)


if __name__ == "__main__":
    unittest.main()
