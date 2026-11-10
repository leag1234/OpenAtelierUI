# SPDX-License-Identifier: BSD-3-Clause
"""Focused tests for measured local notification compatibility."""

import asyncio
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
from open_webui.routers.notifications import (
    get_inbox,
    get_inbox_count,
    open_inbox,
    read_inbox_item,
    router,
)
from fastapi import HTTPException


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

    def test_empty_inbox_projection_matches_measured_contract(self):
        user = SimpleNamespace(id="synthetic-user")
        self.assertEqual(
            asyncio.run(get_inbox(cursor=None, limit=20, user=user)),
            {"items": [], "next_cursor": None},
        )
        self.assertEqual(asyncio.run(get_inbox_count(user=user)), {"count": 0})
        opened = asyncio.run(open_inbox(user=user))
        self.assertEqual(
            {key: opened[key] for key in ("acknowledged_count", "count", "items", "next_cursor")},
            {"acknowledged_count": 0, "count": 0, "items": [], "next_cursor": None},
        )
        self.assertRegex(opened["acknowledged_at"], r"^\d{19}$")

    def test_missing_inbox_item_is_explicit(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(read_inbox_item("missing", user=SimpleNamespace(id="synthetic-user")))
        self.assertEqual((caught.exception.status_code, caught.exception.detail), (404, "Notification not found"))

    def test_inbox_route_inventory(self):
        operations = {
            (method, route.path)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        self.assertTrue(
            {
                ("GET", "/inbox"),
                ("GET", "/inbox/count"),
                ("POST", "/inbox/open"),
                ("PATCH", "/inbox/{id}"),
            }.issubset(operations)
        )


if __name__ == "__main__":
    unittest.main()
