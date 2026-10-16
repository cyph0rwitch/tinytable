import json
import os
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import backend.app.db as database
import backend.app.main as main
from backend.app import seats
from backend.app.db import init_db
from backend.app.main import ChannelSessionIn, CrossTalkIn, KindroidSendIn, runtime
from backend.app.seats import KINDROID, KINDROID_CHANNEL, LOCAL_MODEL, OPERATOR, REPLIKA

NAME_SETTINGS = [key for key in os.environ if key.startswith("TINYTABLE_") and key.endswith(("_NAME", "_PRONOUNS", "_AVATAR"))]


class SeatIdTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_unconfigured_seats_have_neutral_role_names(self):
        with mock.patch.dict(os.environ, {key: "" for key in NAME_SETTINGS}):
            for key in NAME_SETTINGS:
                os.environ.pop(key)
            self.assertEqual(seats.name(REPLIKA), "Replika companion")
            self.assertEqual(seats.name(KINDROID), "Kindroid companion")
            self.assertEqual(seats.name(OPERATOR), "Operator")
            self.assertEqual(seats.possessive(REPLIKA), "their")
            init_db()
            with sqlite3.connect(database.DB_PATH) as db:
                names = dict(db.execute("SELECT id, display_name FROM participants"))
            self.assertEqual(names[REPLIKA], "Replika companion")
            self.assertEqual(names[LOCAL_MODEL], "Local model")

    def test_configured_names_and_pronouns_reach_the_database_and_runtime(self):
        with mock.patch.dict(os.environ, {"TINYTABLE_KINDROID_COMPANION_NAME": "Wren", "TINYTABLE_KINDROID_COMPANION_PRONOUNS": "he/him"}):
            init_db()
            with sqlite3.connect(database.DB_PATH) as db:
                row = db.execute("SELECT display_name, pronouns FROM participants WHERE id=?", (KINDROID,)).fetchone()
            self.assertEqual(row, ("Wren", "he/him"))
            self.assertEqual(seats.possessive(KINDROID), "his")
            self.assertEqual(runtime()["seats"][KINDROID]["name"], "Wren")

    def test_older_ids_and_settings_are_still_accepted(self):
        self.assertEqual(ChannelSessionIn(starting_participant="nova").starting_participant, KINDROID)
        self.assertEqual(CrossTalkIn(body="hi", target="aurora").target, LOCAL_MODEL)
        self.assertEqual(KindroidSendIn(text="hi", channel_id="kindroid:nova-private").channel_id, KINDROID_CHANNEL)
        with mock.patch.dict(os.environ, {"TINYTABLE_LUCY_ONLY": "1", "TINYTABLE_NOVA_ENABLED": "1"}):
            os.environ.pop("TINYTABLE_COMPANIONS_ONLY", None)
            os.environ.pop("TINYTABLE_KINDROID_ENABLED", None)
            self.assertTrue(main.companions_only_mode())
            self.assertTrue(main.kindroid_enabled())
        legacy_routes = {route.path for route in main.app.routes if "/lucy-nova" in getattr(route, "path", "")}
        self.assertIn("/api/channels/lucy-nova/sessions", legacy_routes)

    def test_persona_named_database_is_migrated_once_with_a_backup(self):
        init_db()
        session_id = "session-1"
        with sqlite3.connect(database.DB_PATH) as db:
            db.execute(
                "INSERT INTO messages (id, participant_id, body, created_at, provider, source_type, raw_metadata, mentions) VALUES ('m1', ?, 'Hello.', '2026-01-01', 'kindroid', 'remote_model', ?, ?)",
                (KINDROID, json.dumps({"channel_id": KINDROID_CHANNEL, "speaker": "Nova"}), json.dumps([REPLIKA])),
            )
            db.execute(
                "INSERT INTO conversation_sessions (id, status, starting_participant, expected_participant, started_at, updated_at, mode, heard, awaiting) VALUES (?, 'active', ?, ?, '2026-01-01', '2026-01-01', 'table', ?, ?)",
                (session_id, REPLIKA, KINDROID, json.dumps({REPLIKA: 2, KINDROID: 1}), json.dumps([KINDROID])),
            )
        # Turn the fresh database into one written by a build that used persona IDs.
        legacy = {new: old for old, new in seats.LEGACY_SEATS.items()}
        legacy_channels = {new: old for old, new in seats.LEGACY_CHANNELS.items()}
        db = sqlite3.connect(database.DB_PATH)
        db.execute("PRAGMA foreign_keys = OFF")
        for table, column in database._SEAT_COLUMNS:
            for new, old in legacy.items():
                db.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (old, new))
        for table, column in database._CHANNEL_COLUMNS:
            for new, old in legacy_channels.items():
                db.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (old, new))
        db.execute("UPDATE rooms SET id='channel:lucy-nova' WHERE id=?", (seats.CHANNEL_ROOM_ID,))
        db.execute("UPDATE room_members SET room_id='channel:lucy-nova' WHERE room_id=?", (seats.CHANNEL_ROOM_ID,))
        db.execute("UPDATE messages SET raw_metadata=?, mentions='[\"lucy\"]' WHERE id='m1'", (json.dumps({"channel_id": "kindroid:nova-private", "speaker": "Nova", "emma_note": "Hi."}),))
        db.execute("UPDATE conversation_sessions SET heard='{\"lucy\": 2, \"nova\": 1}', awaiting='[\"nova\"]' WHERE id=?", (session_id,))
        db.commit()
        db.close()

        init_db()
        init_db()  # a second start finds nothing left to migrate

        backups = list(Path(self.directory.name).glob("tinytable.db.pre-seat-ids-*.bak"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(database.DB_PATH) as db:
            ids = {row[0] for row in db.execute("SELECT id FROM participants")}
            self.assertTrue({OPERATOR, REPLIKA, KINDROID, LOCAL_MODEL} <= ids)
            self.assertFalse(ids & set(seats.LEGACY_SEATS))
            participant, metadata, mentions = db.execute("SELECT participant_id, raw_metadata, mentions FROM messages WHERE id='m1'").fetchone()
            self.assertEqual(participant, KINDROID)
            self.assertEqual(json.loads(metadata), {"channel_id": KINDROID_CHANNEL, "speaker": "Nova", "operator_note": "Hi."})
            self.assertEqual(json.loads(mentions), [REPLIKA])
            heard, awaiting, starting = db.execute("SELECT heard, awaiting, starting_participant FROM conversation_sessions WHERE id=?", (session_id,)).fetchone()
            self.assertEqual(json.loads(heard), {REPLIKA: 2, KINDROID: 1})
            self.assertEqual(json.loads(awaiting), [KINDROID])
            self.assertEqual(starting, REPLIKA)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM rooms WHERE id='channel:lucy-nova'").fetchone()[0], 0)
        with sqlite3.connect(backups[0]) as db:
            self.assertEqual(db.execute("SELECT participant_id FROM messages WHERE id='m1'").fetchone()[0], "nova")


if __name__ == "__main__":
    unittest.main()
