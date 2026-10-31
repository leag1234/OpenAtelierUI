#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Offline checks for the isolated terminal download policy helper."""

import json
import importlib.util
import pathlib
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "backend/open_webui/utils/terminal_download_policy.py"
SPEC = importlib.util.spec_from_file_location("terminal_download_policy", POLICY_PATH)
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
TerminalPolicyError = POLICY.TerminalPolicyError
build_download_request = POLICY.build_download_request
parse_terminal_connections = POLICY.parse_terminal_connections
public_terminal_connections = POLICY.public_terminal_connections


SYNTHETIC_KEY = "synthetic-terminal-policy-key"
BASE = "http://127.0.0.1:18081/terminal"


def connection(*, enabled=True, grants=None, key=SYNTHETIC_KEY, auth_type="bearer", url=BASE,
               chat_uploads="default"):
    return {
        "id": "synthetic_terminal",
        "url": url,
        "key": key,
        "auth_type": auth_type,
        "enabled": enabled,
        "name": "Community workshop terminal",
        "config": {
            "chat_uploads": chat_uploads,
            "access_grants": ([{"principal_type": "user", "principal_id": "member-1",
                                "permission": "read"}] if grants is None else grants),
        },
    }


def raw(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class TerminalDownloadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.user = types.SimpleNamespace(id="member-1", role="user")
        self.path = "/files/serve/home/user/requests/chatcmpl-0123456789abcdef0123456789abcdef/Community workshop.pdf"

    def assert_refused(self, config, path=None, user=None):
        with self.assertRaises(TerminalPolicyError) as caught:
            build_download_request(
                config, "synthetic_terminal", self.path if path is None else path,
                self.user if user is None else user,
            )
        self.assertEqual(str(caught.exception), "Terminal download is unavailable.")
        self.assertNotIn(SYNTHETIC_KEY, str(caught.exception))

    def test_exact_verified_identity_request_and_secret_free_projection(self):
        config = raw([connection()])
        request = build_download_request(config, "synthetic_terminal", self.path, self.user)
        expected_path = self.path.lstrip("/").replace("Community workshop.pdf", "Community%20workshop.pdf")
        self.assertEqual(request.path, expected_path)
        self.assertEqual(request.url, BASE + "/" + expected_path)
        self.assertEqual(request.headers, {
            "Authorization": "Bearer " + SYNTHETIC_KEY,
            "X-User-Id": "member-1",
        })
        self.assertNotIn(SYNTHETIC_KEY, repr(request))
        public = public_terminal_connections(config, self.user)
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0], {
            "id": "synthetic_terminal",
            "name": "Community workshop terminal",
            "url": BASE,
            "contexts": {},
            "config": {"chat_uploads": "default"},
        })
        self.assertNotIn("member-1", repr(public))
        filesystem_config = raw([connection(chat_uploads="filesystem")])
        filesystem = public_terminal_connections(filesystem_config, self.user)[0]
        self.assertEqual(filesystem["config"]["chat_uploads"], "filesystem")
        self.assertEqual(build_download_request(
            filesystem_config, "synthetic_terminal", self.path, self.user
        ).headers["X-User-Id"], "member-1")
        self.assertEqual(public_terminal_connections(config, types.SimpleNamespace(id="other", role="user")), [])
        self.assertEqual(public_terminal_connections(raw([connection(enabled=False)]), self.user), [])

    def test_connection_grant_is_not_file_ownership_and_admin_is_not_implicit(self):
        wildcard = raw([connection(grants=[{"principal_type": "user", "principal_id": "*",
                                            "permission": "read"}])])
        admin = types.SimpleNamespace(id="admin-1", role="admin")
        request = build_download_request(wildcard, "synthetic_terminal", self.path, admin)
        self.assertEqual(request.headers["X-User-Id"], "admin-1")
        self.assert_refused(raw([connection()]), user=admin)
        self.assert_refused(raw([connection()]), user=types.SimpleNamespace(id="member-1", role="pending"))
        self.assert_refused(raw([connection(grants=[])]))
        # The request spec forwards verified identity but never asserts file ownership.
        self.assertIn("terminal must", build_download_request.__doc__)

    def test_exact_user_grants_deny_other_identity_and_disabled_connection(self):
        self.assert_refused(raw([connection()]), user=types.SimpleNamespace(id="other", role="user"))
        self.assert_refused(raw([connection(enabled=False)]))
        connections = [connection(), dict(connection(), id="other_terminal")]
        with self.assertRaises(TerminalPolicyError):
            build_download_request(raw(connections), "missing", self.path, self.user)

    def test_strict_configuration_refusals_are_value_free(self):
        valid = connection()
        bad_configs = [
            raw([dict(valid, auth_type="basic")]),
            raw([dict(valid, url="https://user:pass@example.invalid")]),
            raw([dict(valid, url="https://example.invalid/path?key=x")]),
            raw([dict(valid, url="https://example.invalid/path#fragment")]),
            raw([dict(valid, url="https://example.invalid:bad")]),
            raw([dict(valid, url="http://[::1]junk")]),
            raw([dict(valid, url="http://example.invalid\\unexpected")]),
            raw([dict(valid, enabled=1)]),
            raw([dict(valid, config={"chat_uploads": "default", "access_grants": [], "extra": True})]),
            raw([dict(valid, config={"chat_uploads": "default/filesystem", "access_grants": [
                {"principal_type": "group", "principal_id": "g1", "permission": "read"}
            ]})]),
            raw([dict(valid, config={"chat_uploads": "default/filesystem", "access_grants": [
                {"principal_type": "user", "principal_id": "*", "permission": "write"}
            ]})]),
            raw([valid, valid]),
            "[{\"id\":\"a\",\"id\":\"b\"}]",
            "[NaN]",
            "{" + "[" * 1000 + "0" + "]" * 1000 + "}",
            raw([dict(valid, key="secret marker")]),
            raw([dict(valid, name="bad\nname")]),
        ]
        for config in bad_configs:
            with self.subTest(config=config[:50]):
                self.assert_refused(config)
        self.assert_refused(" " * 65537)
        malformed = raw([dict(valid, key="secret\nmarker")])
        with self.assertRaises(TerminalPolicyError) as caught:
            parse_terminal_connections(malformed)
        self.assertNotIn("secret", str(caught.exception))

    def test_file_path_is_single_encoded_fixed_chat_attachment_only(self):
        unicode_path = "/files/serve/home/user/requests/chatcmpl-0123456789abcdef0123456789abcdef/Community workshop — résumé.pdf"
        request = build_download_request(raw([connection()]), "synthetic_terminal", unicode_path, self.user)
        self.assertTrue(request.url.endswith("Community%20workshop%20%E2%80%94%20r%C3%A9sum%C3%A9.pdf"))
        bad_paths = [
            "https://other.invalid" + self.path,
            "//other.invalid" + self.path,
            self.path + "?download=1",
            self.path + "#fragment",
            self.path.replace("requests/", "requests/../"),
            self.path.replace("Community workshop.pdf", ".hidden.pdf"),
            self.path.replace("Community workshop.pdf", "%2e%2e%2Fsecret.pdf"),
            self.path.replace("Community workshop.pdf", "..%2fsecret.pdf"),
            self.path.replace("Community workshop.pdf", "double%252fencoded.pdf"),
            self.path.replace("Community workshop.pdf", "back%5Cslash.pdf"),
            self.path.replace("Community workshop.pdf", "bad%2filename.pdf"),
            self.path.replace("Community workshop.pdf", "bad\ud800.pdf"),
            self.path.replace("0123456789abcdef0123456789abcdef", "ABCDEF0123456789ABCDEF0123456789"),
            self.path.replace("Community workshop.pdf", "two/segments.pdf"),
        ]
        for path in bad_paths:
            with self.subTest(path=path):
                self.assert_refused(raw([connection()]), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
