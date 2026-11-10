# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused security tests for the BSD MFA primitives."""

from __future__ import annotations

import importlib.util
import os
import sys
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class Credential:
    def __init__(self, user_id):
        self.user_id = user_id
        self.secret = None
        self.pending_secret = None
        self.recovery_codes = []
        self.last_totp_step = -1
        self.challenge_nonce = None
        self.challenge_step = None
        self.challenge_expires_at = None
        self.updated_at = int(time.time())


class MfaSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = {}

        def get(user_id):
            return cls.store.get(user_id)

        def update(user_id, **values):
            credential = cls.store.setdefault(user_id, Credential(user_id))
            for key, value in values.items():
                setattr(credential, key, value)
            return credential

        def claim(user_id, step):
            credential = get(user_id)
            if credential is None or credential.last_totp_step >= step:
                return False
            credential.last_totp_step = step
            return True

        def consume(user_id, candidate):
            credential = get(user_id)
            if credential is None or candidate not in credential.recovery_codes:
                return False
            credential.recovery_codes.remove(candidate)
            return True

        env = types.ModuleType("open_webui.env")
        env.WEBUI_SECRET_KEY = "synthetic-focused-test-key"
        model = types.ModuleType("open_webui.models.mfa")
        model.get_mfa_credential = get
        model.list_mfa_credentials = lambda: list(cls.store.values())
        model.update_mfa_credential = update
        model.claim_mfa_totp_step = claim
        model.consume_mfa_recovery_hash = consume
        cls.previous = {
            name: sys.modules.get(name)
            for name in ("open_webui.env", "open_webui.models.mfa")
        }
        sys.modules["open_webui.env"] = env
        sys.modules["open_webui.models.mfa"] = model
        spec = importlib.util.spec_from_file_location(
            "openatelier_mfa_security_under_test", ROOT / "utils" / "mfa.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        for name, previous in cls.previous.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    def setUp(self):
        self.store.clear()
        os.environ.pop("MFA_ENCRYPTION_KEY", None)

    def test_challenge_is_bounded_latest_only_and_consumable(self):
        user_id = str(uuid.uuid4())
        first = self.module.issue_challenge(user_id, "enroll")
        self.assertLessEqual(len(first["challenge_token"]), 160)
        second = self.module.issue_challenge(user_id, "enroll")
        with self.assertRaises(self.module.MfaError):
            self.module.validate_challenge(first["challenge_token"], "enroll")
        self.assertEqual(
            self.module.validate_challenge(second["challenge_token"], "enroll")[0],
            user_id,
        )
        self.module.consume_challenge(user_id)
        with self.assertRaises(self.module.MfaError):
            self.module.validate_challenge(second["challenge_token"], "enroll")

    def test_secret_is_encrypted_and_wrong_key_is_rejected(self):
        plaintext = self.module.new_totp_secret()
        encrypted = self.module.encrypt_secret(plaintext)
        self.assertNotIn(plaintext, encrypted)
        self.assertEqual(self.module.decrypt_secret(encrypted), plaintext)
        os.environ["MFA_ENCRYPTION_KEY"] = self.module.Fernet.generate_key().decode()
        with self.assertRaises(self.module.MfaError):
            self.module.decrypt_secret(encrypted)

    def test_totp_step_and_recovery_code_are_single_use(self):
        user_id = str(uuid.uuid4())
        secret = self.module.new_totp_secret()
        self.store[user_id] = Credential(user_id)
        step = int(time.time()) // 30
        code = self.module._totp_value(secret, step)
        with patch.object(self.module.time, "time", return_value=step * 30 + 1):
            self.assertTrue(self.module.verify_totp(user_id, secret, code))
            self.assertFalse(self.module.verify_totp(user_id, secret, code))
        codes, hashes = self.module.new_recovery_codes(user_id)
        self.store[user_id].recovery_codes = hashes
        self.assertEqual(len(codes), self.module.RECOVERY_CODE_COUNT)
        self.assertTrue(self.module.consume_recovery_code(user_id, codes[0]))
        self.assertFalse(self.module.consume_recovery_code(user_id, codes[0]))


if __name__ == "__main__":
    unittest.main()
