import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_store():
    spec = importlib.util.spec_from_file_location(
        "calendar_store_test", ROOT / "backend/open_webui/models/calendars.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CalendarStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=str(Path(tempfile.gettempdir()).resolve()))
        self.path = Path(self.temp.name) / "webui.db"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE existing_fixture (id TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO existing_fixture VALUES ('keep','unchanged')")
        self.module = load_store()
        self.store = self.module.Calendars("sqlite:///" + str(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def test_prefixed_schema_preserves_existing_data_and_reopens(self):
        self.store.ensure_personal("owner")
        calendars = self.store.list_readable("owner", ())
        self.assertEqual(len(calendars), 1)
        self.assertEqual(calendars[0]["name"], "Personal")
        self.assertTrue(calendars[0]["is_default"])
        reopened = self.module.Calendars("sqlite:///" + str(self.path))
        self.assertEqual(reopened.list_readable("owner", ())[0]["id"], calendars[0]["id"])
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT * FROM existing_fixture").fetchall(), [("keep", "unchanged")])

    def test_admin_system_calendar_and_grant_order_are_stable(self):
        self.store.ensure_personal("admin")
        self.store.ensure_system("admin")
        values = self.store.list_readable("admin", ())
        self.assertEqual([item["id"] for item in values], [values[0]["id"], "__scheduled_tasks__"])
        self.assertEqual(values[1]["name"], "Scheduled Tasks")
        self.assertEqual(values[1]["color"], "#8b5cf6")
        self.assertTrue(values[1]["is_system"])
        calendar = self.store.create("owner", "Ordered", None, None, None, [
            {"principal_type": "user", "principal_id": "reader", "permission": "read"},
            {"principal_type": "user", "principal_id": "writer", "permission": "write"},
        ])
        self.assertEqual([item["permission"] for item in calendar["access_grants"]], ["read", "write"])

    def test_distinct_read_and_write_grants_match_measured_access(self):
        calendar = self.store.create(
            "owner", "Community festival", "#336699",
            json.dumps({"description": "Synthetic"}), json.dumps({"round": 21}),
            [
                {"principal_type": "user", "principal_id": "reader", "permission": "read"},
                {"principal_type": "group", "principal_id": "editors", "permission": "write"},
            ],
        )
        self.assertTrue(self.store.can_read(calendar, "reader", ()))
        self.assertFalse(self.store.can_write(calendar, "reader", ()))
        self.assertFalse(self.store.can_read(calendar, "writer", ("editors",)))
        self.assertTrue(self.store.can_write(calendar, "writer", ("editors",)))
        self.assertTrue(self.store.can_read(calendar, "admin", (), admin=True))
        self.assertEqual([item["id"] for item in self.store.list_readable("reader", ())], [calendar["id"]])
        self.assertEqual(self.store.list_readable("writer", ("editors",)), [])

    def test_event_attendee_range_update_rsvp_delete_and_cascade(self):
        calendar = self.store.create("owner", "Plan", None, None, None, [])
        event = self.store.create_event(
            "owner",
            {
                "calendar_id": calendar["id"], "title": "Lantern session",
                "start_at": 1893456000000000000, "end_at": 1893459600000000000,
                "data": json.dumps({"agenda": ["layout"]}), "meta": None,
            },
            [{"user_id": "reader", "status": "pending", "meta": json.dumps({"synthetic": True})}],
        )
        self.assertEqual(event["attendees"][0]["status"], "pending")
        self.assertTrue(self.store.rsvp(event["id"], "reader", "accepted"))
        self.assertFalse(self.store.rsvp(event["id"], "foreign", "accepted"))
        updated = self.store.update_event(event["id"], {"title": "Lantern revised"})
        self.assertEqual(updated["title"], "Lantern revised")
        self.assertEqual(updated["attendees"][0]["status"], "accepted")
        events, total = self.store.list_events(
            [calendar["id"]], start_at=1893450000000000000,
            end_at=1893460000000000000, query="lantern", skip=0, limit=30,
        )
        self.assertEqual(total, 1)
        self.assertEqual(events[0]["id"], event["id"])
        self.assertTrue(self.store.delete(calendar["id"]))
        self.assertIsNone(self.store.get_event(event["id"]))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM openatelier_calendar_attendee").fetchone()[0], 0)

    def test_default_switch_update_and_event_delete(self):
        self.store.ensure_personal("owner")
        created = self.store.create("owner", "Second", None, None, None, [])
        selected = self.store.set_default(created["id"], "owner")
        self.assertTrue(selected["is_default"])
        self.assertIsNone(self.store.set_default(created["id"], "reader"))
        updated = self.store.update(created["id"], {"name": "Renamed"})
        self.assertEqual(updated["name"], "Renamed")
        event = self.store.create_event("owner", {
            "calendar_id": created["id"], "title": "Temporary", "start_at": 1,
        }, [])
        self.assertTrue(self.store.delete_event(event["id"]))
        self.assertIsNone(self.store.get_event(event["id"]))

    def test_unsupported_database_and_schema_collision_refuse(self):
        with self.assertRaises(self.module.CalendarsUnavailable):
            self.module.Calendars("postgresql://example/db")
        with self.assertRaises(self.module.CalendarsUnavailable):
            self.module.Calendars("sqlite:///:memory:")
        broken = Path(self.temp.name) / "broken.db"
        with closing(sqlite3.connect(broken)) as db, db:
            db.execute("CREATE TABLE openatelier_calendar (id TEXT)")
        with self.assertRaises(self.module.CalendarsUnavailable):
            self.module.Calendars("sqlite:///" + str(broken))


if __name__ == "__main__":
    unittest.main(verbosity=2)
