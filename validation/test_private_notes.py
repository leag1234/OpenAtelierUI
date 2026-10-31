import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_store():
    spec = importlib.util.spec_from_file_location(
        "private_notes_store_test", ROOT / "backend/open_webui/models/notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrivateNotesStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.path = Path(self.temp.name) / "webui.db"
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE existing_fixture (id TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO existing_fixture VALUES ('keep','unchanged')")
        self.store_module = load_store()
        self.store = self.store_module.PrivateNotes("sqlite:///" + str(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def add_note(self, note_id, user_id, title, data=None, meta=None):
        import json
        now = 100
        return self.store.create({
            "id": note_id, "user_id": user_id, "title": title,
            "data": None if data is None else json.dumps(data, separators=(",", ":")),
            "meta": None if meta is None else json.dumps(meta, separators=(",", ":")),
            "created_at": now, "updated_at": now,
        })

    def test_prepares_only_prefixed_table_and_preserves_existing_rows(self):
        tables = {row[0] for row in sqlite3.connect(self.path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"existing_fixture", "openatelier_private_note"})
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT * FROM existing_fixture").fetchall(), [("keep", "unchanged")])
        indexes = {row[1] for row in sqlite3.connect(self.path).execute(
            "PRAGMA index_list(openatelier_private_note)")}
        self.assertIn("openatelier_private_note_owner_time", indexes)

    def test_private_crud_pin_search_and_reopen_persistence(self):
        note = self.add_note("n1", "owner", "Workshop plan 20%_", {"venue": "Hall"}, {"source": "user"})
        self.assertEqual(note["data"], {"venue": "Hall"})
        self.assertEqual(note["meta"], {"source": "user"})
        self.assertEqual(note["access_grants"], [])
        self.assertIsNone(self.store.get_owned("n1", "other"))
        self.assertEqual(self.store.list_owned("other", 32, 0)[0], [])
        rows, count = self.store.list_owned("owner", 32, 0, query="20%_")
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]["id"], "n1")
        pinned = self.store.toggle_pin("n1", "owner")
        self.assertTrue(pinned["is_pinned"])
        self.assertEqual(pinned["updated_at"], 100)
        self.assertEqual(pinned["data"], {"venue": "Hall"})
        self.assertEqual([row["id"] for row in self.store.list_owned("owner", 32, 0, pinned=True)[0]], ["n1"])
        updated = self.store.update_owned("n1", "owner", "Revised plan", '{"venue":"Room B"}', None, 102)
        self.assertEqual(updated["created_at"], 100)
        self.assertEqual(updated["data"], {"venue": "Room B"})
        self.assertTrue(updated["is_pinned"])
        reopened = self.store_module.PrivateNotes("sqlite:///" + str(self.path))
        self.assertEqual(reopened.get_owned("n1", "owner"), updated)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_note("n1", "other", "collision")
        self.assertEqual(reopened.get_owned("n1", "owner"), updated)

    def test_json_null_and_sql_null_data_share_public_projection(self):
        self.store.create({"id": "json-null", "user_id": "owner", "title": "JSON null",
                           "data": "null", "meta": "null", "created_at": 21, "updated_at": 22})
        self.store.create({"id": "sql-null", "user_id": "owner", "title": "SQL null",
                           "data": None, "meta": None, "created_at": 23, "updated_at": 24})
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE openatelier_private_note SET is_pinned=1 WHERE id='json-null'")
            stored = db.execute(
                "SELECT id,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note "
                "WHERE id IN ('json-null','sql-null') ORDER BY id"
            ).fetchall()
        self.assertEqual(stored, [("json-null", "null", "null", 21, 22, 1),
                                  ("sql-null", None, None, 23, 24, 0)])
        for note_id, created, updated, pinned in (("json-null", 21, 22, True), ("sql-null", 23, 24, False)):
            note = self.store.get_owned(note_id, "owner")
            self.assertEqual(note["data"], {})
            self.assertIsNone(note["meta"])
            self.assertEqual((note["created_at"], note["updated_at"], note["is_pinned"]),
                             (created, updated, pinned))
        listed = {row["id"]: row for row in self.store.list_owned("owner", 32, 0)[0]}
        self.assertEqual(listed["json-null"]["data"], {})
        self.assertEqual(listed["sql-null"]["data"], {})
        self.assertEqual(sqlite3.connect(self.path).execute(
            "SELECT data FROM openatelier_private_note WHERE id='json-null'").fetchone(), ("null",))

    def test_partial_updates_preserve_raw_fields_and_enforce_merged_limit_atomically(self):
        self.store.create({"id": "partial", "user_id": "owner", "title": "Original",
                           "data": '{"content":{"md":"Keep"},"counter":90071992547409931234}',
                           "meta": '{"source":"retained","counter":90071992547409931234}',
                           "created_at": 1792949159000000000, "updated_at": 1792949160000000000})
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE openatelier_private_note SET is_pinned=1 WHERE id='partial'")
        before = sqlite3.connect(self.path).execute(
            "SELECT data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE id='partial'"
        ).fetchone()
        title_only = self.store.update_owned("partial", "owner", "Renamed", None, None, 1792949170000000000,
                                             update_data=False, update_meta=False)
        after_title = sqlite3.connect(self.path).execute(
            "SELECT data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE id='partial'"
        ).fetchone()
        self.assertEqual(after_title, (before[0], before[1], before[2], 1792949170000000000, 1))
        self.assertEqual(title_only["data"]["counter"], 90071992547409931234)
        data_only = self.store.update_owned("partial", "owner", "Renamed", '{"content":{"md":"Changed"}}',
                                            None, 1792949180000000000, update_data=True, update_meta=False)
        raw = sqlite3.connect(self.path).execute(
            "SELECT data,meta,created_at,is_pinned FROM openatelier_private_note WHERE id='partial'"
        ).fetchone()
        self.assertEqual(raw, ('{"content":{"md":"Changed"}}', before[1], before[2], 1))
        self.assertEqual(data_only["meta"]["counter"], 90071992547409931234)
        self.assertIsNone(self.store.update_owned("partial", "other", "No access", None, None, 9,
                                                  update_data=False, update_meta=False))
        with self.assertRaises(ValueError):
            self.store.update_owned("partial", "owner", "Too large", '{"content":{"md":"x"}}',
                                    '{"extra":"' + ('z' * 32768) + '"}', 1792949190000000000,
                                    update_data=True, update_meta=True)
        final = sqlite3.connect(self.path).execute(
            "SELECT title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE id='partial'"
        ).fetchone()
        self.assertEqual(final, ("Renamed", '{"content":{"md":"Changed"}}', before[1], before[2],
                                 1792949180000000000, 1))

    def test_private_notes_permission_and_grants_route_checks(self):
        env = types.ModuleType("open_webui.env")
        env.DATABASE_URL = "sqlite:///" + str(self.path)
        model_module = self.store_module
        auth = types.ModuleType("open_webui.utils.auth")
        auth.get_verified_user = lambda: None
        group_module = types.ModuleType("open_webui.models.groups")
        class GroupsStub:
            denied = False

            @classmethod
            def get_groups_by_member_id(cls, _user_id):
                return [SimpleNamespace(permissions={"workspace": {"notes": False}})] if cls.denied else []
        group_module.Groups = GroupsStub
        for name in ("open_webui", "open_webui.models", "open_webui.utils"):
            sys.modules.setdefault(name, types.ModuleType(name))
        old = {key: sys.modules.get(key) for key in (
            "open_webui.env", "open_webui.models.notes", "open_webui.models.groups", "open_webui.utils.auth")}
        sys.modules["open_webui.env"] = env
        sys.modules["open_webui.models.notes"] = model_module
        sys.modules["open_webui.models.groups"] = group_module
        sys.modules["open_webui.utils.auth"] = auth
        old_flag = os.environ.get("OPENATELIER_PRIVATE_NOTES_COMPAT")
        os.environ["OPENATELIER_PRIVATE_NOTES_COMPAT"] = "1"
        try:
            spec = importlib.util.spec_from_file_location(
                "private_notes_router_test", ROOT / "backend/open_webui/routers/notes.py"
            )
            router = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(router)
            self.assertEqual(set(router.NoteForm.model_fields), {"title", "data", "meta", "access_grants"})
            self.assertEqual(set(router.NoteItemResponse.model_fields), {
                "id", "title", "data", "updated_at", "created_at", "is_pinned", "user"})
            self.assertEqual(set(router.NoteResponse.model_fields), {
                "id", "user_id", "title", "created_at", "updated_at", "data", "meta",
                "access_grants", "is_pinned", "write_access"})
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                config=SimpleNamespace(USER_PERMISSIONS={"workspace": {"notes": True}}))))
            owner = SimpleNamespace(id="owner", name="Workshop member", role="user", email="member@example.invalid")
            created_ns, updated_ns = 1792949159000000000, 1792949160000000000
            with patch.object(router.time, "time_ns", side_effect=[created_ns, updated_ns]):
                created = router.create_note(request, router.NoteForm(title="Private", data=None, meta=None), owner)
                pinned_note = router.pin_note(request, created["id"], owner)
                updated_note = router.update_note(
                    request, created["id"], router.NoteForm(title="Private", data=None, meta=None), owner
                )
            self.assertEqual(created["user_id"], "owner")
            self.assertEqual(created["created_at"], created_ns)
            self.assertEqual(created["updated_at"], created_ns)
            self.assertTrue(pinned_note["is_pinned"])
            self.assertEqual(updated_note["created_at"], created_ns)
            self.assertEqual(updated_note["updated_at"], updated_ns)
            self.assertTrue(updated_note["is_pinned"])
            self.assertEqual(created["data"], {})
            self.assertIsNone(created["meta"])
            self.assertEqual(created["access_grants"], [])
            original_data, original_meta = created["data"], created["meta"]
            title_only = router.NoteForm(title="Private title", data=None, meta=None)
            changed_title = router.update_note(request, created["id"], title_only, owner)
            self.assertEqual((changed_title["data"], changed_title["meta"]), (original_data, original_meta))
            meta_only = router.NoteForm(title="Private metadata", meta={"source": "kept"})
            changed_meta = router.update_note(request, created["id"], meta_only, owner)
            self.assertEqual(changed_meta["data"], original_data)
            self.assertEqual(changed_meta["meta"], {"source": "kept"})
            old_state = sqlite3.connect(self.path).execute(
                "SELECT title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE id=?",
                (created["id"],),
            ).fetchone()
            too_large = router.NoteForm(title="Rejected", meta={"large": "x" * 32768})
            with self.assertRaises(router.HTTPException) as oversized:
                router.update_note(request, created["id"], too_large, owner)
            self.assertEqual(oversized.exception.status_code, 422)
            new_state = sqlite3.connect(self.path).execute(
                "SELECT title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note WHERE id=?",
                (created["id"],),
            ).fetchone()
            self.assertEqual(new_state, old_state)
            large_meta = '{"x":"' + ('m' * 32600) + '"}'
            router.notes_store.create({"id": "near-limit", "user_id": "owner", "title": "x",
                                       "data": None, "meta": large_meta,
                                       "created_at": created_ns, "updated_at": created_ns})
            near_limit_before = sqlite3.connect(self.path).execute(
                "SELECT title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note "
                "WHERE id='near-limit'"
            ).fetchone()
            with self.assertRaises(router.HTTPException) as merged_oversized:
                router.update_note(request, "near-limit", router.NoteForm(title="T" * 500), owner)
            self.assertEqual(merged_oversized.exception.status_code, 422)
            self.assertEqual(sqlite3.connect(self.path).execute(
                "SELECT title,data,meta,created_at,updated_at,is_pinned FROM openatelier_private_note "
                "WHERE id='near-limit'"
            ).fetchone(), near_limit_before)
            denied = SimpleNamespace(id="other", role="user")
            with self.assertRaises(router.HTTPException) as caught:
                router.get_note(request, created["id"], denied)
            self.assertEqual(caught.exception.status_code, 403)
            false_request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                config=SimpleNamespace(USER_PERMISSIONS=SimpleNamespace(
                    value={"workspace": {"notes": False}})))))
            with self.assertRaises(router.HTTPException) as disabled:
                router.list_notes(false_request, 1, owner)
            self.assertEqual(disabled.exception.status_code, 403)
            GroupsStub.denied = True
            with self.assertRaises(router.HTTPException) as group_disabled:
                router.list_notes(request, 1, owner)
            self.assertEqual(group_disabled.exception.status_code, 403)
            GroupsStub.denied = False
            for page in (0, 9999999):
                with self.assertRaises(router.HTTPException) as invalid_page:
                    router.list_notes(request, page, owner)
                self.assertEqual(invalid_page.exception.status_code, 422)
            result = router.query_private_notes(request, query="Private", page=1, user=owner)
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["user"], {
                "id": "owner", "name": "Workshop member", "role": "user",
                "email": "member@example.invalid",
            })
            self.assertEqual([row["id"] for row in router.list_pinned_notes(request, owner)], [created["id"]])
            with self.assertRaises(router.HTTPException) as sharing:
                router._form_values(router.NoteForm(title="Private", access_grants=[{"permission": "read"}]))
            self.assertEqual(sharing.exception.status_code, 422)
            bad = router.NoteForm(title="Private", data={"x": float("nan")})
            with self.assertRaises(router.HTTPException) as nonfinite:
                router._form_values(bad)
            self.assertEqual(nonfinite.exception.status_code, 422)
            deep = value = {}
            for _ in range(66):
                value["nested"] = {}
                value = value["nested"]
            with self.assertRaises(router.HTTPException) as excessive_depth:
                router._form_values(router.NoteForm(title="Private", data=deep))
            self.assertEqual(excessive_depth.exception.status_code, 422)
            self.assertEqual(router.notes_store.list_owned("owner", 32, 0)[1], 2)
            self.assertIsNone(router._load_notes_store("postgresql://example/db"))
            stored_notes = router.notes_store
            router.notes_store = None
            GroupsStub.denied = True
            with self.assertRaises(router.HTTPException) as unavailable:
                router.list_notes(request, 1, owner)
            self.assertEqual(unavailable.exception.status_code, 503)
            router.notes_store = stored_notes
            disabled_path = Path(self.temp.name) / "default-off.db"
            with sqlite3.connect(disabled_path) as db:
                db.execute("CREATE TABLE existing_only (id TEXT)")
            original_url = env.DATABASE_URL
            env.DATABASE_URL = "sqlite:///" + str(disabled_path)
            os.environ.pop("OPENATELIER_PRIVATE_NOTES_COMPAT", None)
            off_spec = importlib.util.spec_from_file_location(
                "private_notes_router_off_test", ROOT / "backend/open_webui/routers/notes.py"
            )
            off_router = importlib.util.module_from_spec(off_spec)
            off_spec.loader.exec_module(off_router)
            self.assertIsNone(off_router.notes_store)
            with sqlite3.connect(disabled_path) as db:
                self.assertEqual(db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall(), [("existing_only",)])
            env.DATABASE_URL = original_url
        finally:
            if old_flag is None:
                os.environ.pop("OPENATELIER_PRIVATE_NOTES_COMPAT", None)
            else:
                os.environ["OPENATELIER_PRIVATE_NOTES_COMPAT"] = old_flag
            for key, value in old.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

    def test_non_sqlite_memory_symlink_and_schema_collision_refuse(self):
        with self.assertRaises(self.store_module.PrivateNotesUnavailable):
            self.store_module.PrivateNotes("postgresql://example/db")
        with self.assertRaises(self.store_module.PrivateNotesUnavailable):
            self.store_module.PrivateNotes("sqlite:///:memory:")
        link = Path(self.temp.name) / "link.db"
        link.symlink_to(self.path)
        with self.assertRaises(self.store_module.PrivateNotesUnavailable):
            self.store_module.PrivateNotes("sqlite:///" + str(link))
        broken = Path(self.temp.name) / "broken.db"
        with sqlite3.connect(broken) as db:
            db.execute("CREATE TABLE openatelier_private_note (id TEXT)")
        with self.assertRaises(self.store_module.PrivateNotesUnavailable):
            self.store_module.PrivateNotes("sqlite:///" + str(broken))


if __name__ == "__main__":
    unittest.main(verbosity=2)
