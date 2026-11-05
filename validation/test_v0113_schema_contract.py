# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Black-box v0.11.3 schema contract checks for migration coverage."""

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "validation"))
import migrate_sqlite
from test_migrate_sqlite import build_source, build_template


# Generated from a fresh opaque ghcr.io/open-webui/open-webui:v0.11.3 runtime.
# It records only table and column names, never source code or user data.
V0113_COLUMNS = {
    "access_grant": "id resource_type resource_id principal_type principal_id permission created_at",
    "alembic_version": "version_num",
    "api_key": "id user_id key data expires_at last_used_at created_at updated_at",
    "auth": "id email password active",
    "automation": "id user_id name data meta is_active last_run_at next_run_at created_at updated_at folder_id",
    "automation_run": "id automation_id chat_id status error created_at",
    "calendar": "id user_id name color is_default data meta created_at updated_at",
    "calendar_event": "id calendar_id user_id title description start_at end_at all_day rrule color location data meta is_cancelled created_at updated_at",
    "calendar_event_attendee": "id event_id user_id status meta created_at updated_at",
    "channel": "id user_id name description data meta created_at updated_at type is_private archived_at archived_by deleted_at deleted_by updated_by",
    "channel_file": "id user_id channel_id file_id created_at updated_at message_id",
    "channel_member": "id channel_id user_id created_at status is_active is_channel_muted is_channel_pinned data meta joined_at left_at last_read_at updated_at role invited_by invited_at",
    "channel_webhook": "id user_id channel_id name profile_image_url token last_used_at created_at updated_at",
    "chat": "id user_id title created_at updated_at share_id archived chat pinned meta folder_id tasks summary last_read_at current_message_id variables timer_at",
    "chat_file": "id user_id chat_id file_id message_id created_at updated_at",
    "chat_message": "id chat_id user_id role parent_id content output model_id files sources embeds done status_history error usage created_at updated_at context_summary meta",
    "chatidtag": "id tag_name chat_id user_id timestamp",
    "config": "key value updated_at",
    "config_old": "id data version created_at updated_at",
    "document": "collection_name name title filename content user_id timestamp",
    "feedback": "id user_id version type data meta snapshot created_at updated_at",
    "file": "id user_id filename meta created_at hash data updated_at path",
    "folder": "id parent_id user_id name items meta is_expanded created_at updated_at data",
    "function": "id user_id name type content meta valves is_active is_global updated_at created_at",
    "group": "id user_id name description data meta permissions created_at updated_at",
    "group_member": "id group_id user_id created_at updated_at",
    "knowledge": "id user_id name description meta created_at updated_at data",
    "knowledge_directory": "id knowledge_id parent_id name user_id created_at updated_at",
    "knowledge_file": "id user_id knowledge_id file_id created_at updated_at directory_id",
    "memory": "id user_id content updated_at created_at type path meta",
    "message": "id user_id channel_id content data meta created_at updated_at parent_id reply_to_id is_pinned pinned_at pinned_by",
    "message_reaction": "id user_id message_id name created_at",
    "model": "id user_id base_model_id name params meta updated_at created_at is_active",
    "note": "id user_id title data meta created_at updated_at",
    "oauth_session": "id user_id provider token expires_at created_at updated_at",
    "pinned_note": "id user_id note_id created_at",
    "prompt": "id command user_id name content data meta is_active version_id tags created_at updated_at",
    "prompt_history": "id prompt_id parent_id snapshot user_id commit_message created_at",
    "shared_chat": "id chat_id user_id title chat created_at updated_at",
    "skill": "id user_id name description content meta is_active updated_at created_at",
    "tag": "id name user_id meta",
    "tool": "id user_id name content specs meta valves updated_at created_at",
    "user": "id name email role profile_image_url last_active_at updated_at created_at username bio gender date_of_birth profile_banner_image_url timezone presence_state status_emoji status_message status_expires_at oauth info settings scim variables",
}
V0113_COLUMNS = {table: set(columns.split()) for table, columns in V0113_COLUMNS.items()}


class V0113SchemaContractTests(unittest.TestCase):
    def test_every_reference_table_is_classified(self):
        self.assertEqual(set(V0113_COLUMNS) - migrate_sqlite.KNOWN_TABLES, set())

    def test_copy_relevant_reference_shapes_are_fully_accounted_for(self):
        self.assertEqual(V0113_COLUMNS["folder"], migrate_sqlite.FOLDER_V0113_COLUMNS)
        self.assertEqual(V0113_COLUMNS["file"], migrate_sqlite.SOURCE_REQUIRED["file"])
        self.assertEqual(V0113_COLUMNS["function"], migrate_sqlite.SOURCE_REQUIRED["function"])
        self.assertEqual(
            V0113_COLUMNS["chat"],
            migrate_sqlite.SOURCE_REQUIRED["chat"] | migrate_sqlite.CHAT_UNSUPPORTED,
        )
        self.assertEqual(
            V0113_COLUMNS["chat_message"],
            {"id", "chat_id", "user_id", "role", "parent_id", "content"}
            | migrate_sqlite.MESSAGE_UNSUPPORTED,
        )
        self.assertEqual(
            V0113_COLUMNS["tool"], migrate_sqlite.SOURCE_REQUIRED["tool"] - {"version_id"}
        )
        self.assertEqual(
            V0113_COLUMNS["model"], migrate_sqlite.SOURCE_REQUIRED["model"] - {"version_id"}
        )

    def test_populated_v0113_tool_without_version_id_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            uploads = root / "uploads"
            template = root / "template.db"
            build_source(source, uploads)
            build_template(template)
            database = sqlite3.connect(source)
            database.execute("UPDATE alembic_version SET version_num=?", (migrate_sqlite.V0113_ALEMBIC_HEAD,))
            database.execute("ALTER TABLE tool RENAME TO tool_with_version")
            database.execute("CREATE TABLE tool(id TEXT,user_id TEXT,name TEXT,content TEXT,specs TEXT,meta TEXT,valves TEXT,updated_at INTEGER,created_at INTEGER)")
            database.execute("INSERT INTO tool SELECT id,user_id,name,content,specs,meta,valves,updated_at,created_at FROM tool_with_version")
            database.execute("DROP TABLE tool_with_version")
            database.execute("ALTER TABLE model RENAME TO model_with_version")
            database.execute("CREATE TABLE model(id TEXT,user_id TEXT,base_model_id TEXT,name TEXT,params TEXT,meta TEXT,updated_at INTEGER,created_at INTEGER,is_active INTEGER)")
            database.execute("INSERT INTO model SELECT id,user_id,base_model_id,name,params,meta,updated_at,created_at,is_active FROM model_with_version")
            database.execute("DROP TABLE model_with_version")
            database.commit()
            database.close()
            report = migrate_sqlite.preview(source, uploads, template)
            fatal_codes = {entry["code"] for entry in report["blockers"] if entry.get("fatal")}
            self.assertNotIn("source_schema_mismatch_tool", fatal_codes)
            self.assertNotIn("source_schema_mismatch_model", fatal_codes)

    def test_contract_identity_is_pinned(self):
        self.assertEqual(migrate_sqlite.V0113_ALEMBIC_HEAD, "d4c1a8e37b62")
        self.assertEqual(len(V0113_COLUMNS), 43)


if __name__ == "__main__":
    unittest.main()
