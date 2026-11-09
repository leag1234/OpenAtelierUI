# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for measured local notification compatibility."""

import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from open_webui.utils.notification_compat import (
    NotificationInputError,
    load_targets,
    normalized_target,
    public_target,
    validated_url,
)


class NotificationCompatTests(unittest.TestCase):
    def setUp(self):
        self.form = {
            "id": "Work/Alert",
            "type": "webhook",
            "enabled": False,
            "events": ["chat.finished", "chat.failed"],
            "delivery": "away",
            "config": {"url": "https://example.com/path/delivered"},
        }

    @patch("open_webui.utils.notification_compat._resolved_public_addresses", return_value=["93.184.216.34"])
    def test_normalizes_and_masks_target(self, _resolver):
        row = normalized_target(self.form)
        self.assertEqual(row["id"], "work-alert")
        self.assertEqual(public_target(row)["config"]["url_masked"], "https://example.com/...ered")
        self.assertNotIn("url", public_target(row)["config"])

    @patch("open_webui.utils.notification_compat._resolved_public_addresses", return_value=["93.184.216.34"])
    def test_update_preserves_created_and_default(self, _resolver):
        previous = normalized_target(self.form)
        previous["is_default"] = True
        updated = normalized_target({**self.form, "events": ["channel.message"]}, previous=previous)
        self.assertEqual(updated["created_at"], previous["created_at"])
        self.assertTrue(updated["is_default"])
        self.assertEqual(updated["events"], ["channel.message"])

    @patch("open_webui.utils.notification_compat._resolved_public_addresses", return_value=["93.184.216.34"])
    def test_rejects_unknown_event(self, _resolver):
        with self.assertRaisesRegex(NotificationInputError, "unsupported_event:chat"):
            normalized_target({**self.form, "events": ["chat"]})

    @patch("open_webui.utils.notification_compat.socket.getaddrinfo")
    def test_rejects_private_resolved_address(self, getaddrinfo):
        getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 443))]
        with self.assertRaisesRegex(NotificationInputError, "invalid_url"):
            validated_url("https://example.com/hook")

    @patch("open_webui.utils.notification_compat._resolved_public_addresses", return_value=["93.184.216.34"])
    def test_rejects_empty_normalized_identifier(self, _resolver):
        with self.assertRaisesRegex(NotificationInputError, "invalid_id"):
            normalized_target({**self.form, "id": "///"})

    def test_loads_only_dictionary_rows(self):
        user = SimpleNamespace(settings={"openatelier_notification_targets": {"targets": [{"id": "a"}, "bad"]}})
        self.assertEqual(load_targets(user), [{"id": "a"}])


if __name__ == "__main__":
    unittest.main()
