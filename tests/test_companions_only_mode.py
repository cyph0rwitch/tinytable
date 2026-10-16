import asyncio
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.app.db as database
from backend.app.db import init_db
from backend.app.main import (
    REPLIKA_CONNECTION_STATUS,
    ReplikaSendIn,
    ReplikaStatusIn,
    app,
    current_replika_connection_status,
    messages,
    participants,
    provider_channels,
    save_message,
    send_direct_to_replika,
    update_replika_status,
)
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import KINDROID, OPERATOR, REPLIKA


class CompanionsOnlyModeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()
        self.original_replika_status = dict(REPLIKA_CONNECTION_STATUS)
        REPLIKA_CONNECTION_STATUS.update({"state": "starting", "last_transition_at": None, "last_heartbeat_at": None})
        self.mode = patch.dict(os.environ, {"TINYTABLE_COMPANIONS_ONLY": "1"})
        self.mode.start()

    def tearDown(self):
        self.mode.stop()
        REPLIKA_CONNECTION_STATUS.clear()
        REPLIKA_CONNECTION_STATUS.update(self.original_replika_status)
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_only_operator_and_replika_are_listed_and_returned(self):
        save_message(OPERATOR, "Hello Juniper.", "human", "manual-entry")
        save_message(REPLIKA, "Hello Sam.", "remote_model", "replika")
        save_message(KINDROID, "Hidden from this view.", "remote_model", "kindroid")

        self.assertEqual([item["id"] for item in participants()], [OPERATOR, REPLIKA])
        self.assertEqual(provider_channels(), [])
        self.assertEqual([item["participant_id"] for item in messages()], [OPERATOR, REPLIKA])

    def test_explicit_created_at_keeps_provider_reply_after_outgoing_message(self):
        save_message(OPERATOR, "hihi", "human", "replika", created_at="2026-10-09T04:29:22+00:00")
        save_message(REPLIKA, "you came back", "remote_model", "replika", created_at="2026-10-09T04:29:23+00:00")

        self.assertEqual([item["participant_id"] for item in messages()], [OPERATOR, REPLIKA])

    def test_direct_send_reserves_outgoing_timestamp_before_provider_reply(self):
        timestamps = iter(("2026-10-09T04:30:00+00:00", "2026-10-09T04:30:01+00:00"))

        async def fake_provider(_request):
            save_message(REPLIKA, "reply", "remote_model", "replika", request_id="reply")
            return {"provider_message_id": "reply"}

        with patch("backend.app.main.now", side_effect=lambda: next(timestamps)), patch(
            "backend.app.main.send_to_replika", side_effect=fake_provider
        ):
            asyncio.run(send_direct_to_replika(ReplikaSendIn(text="hihi", confirm_external_send=True)))

        self.assertEqual([item["participant_id"] for item in messages()], [OPERATOR, REPLIKA])

    def test_runtime_reports_live_receiver_status_and_stale_transition(self):
        connected = update_replika_status(ReplikaStatusIn(state="connected"))
        self.assertEqual(connected["state"], "connected")
        self.assertEqual(current_replika_connection_status(True, True)["state"], "connected")

        REPLIKA_CONNECTION_STATUS["last_heartbeat_at"] = "2000-01-01T00:00:00+00:00"
        self.assertEqual(current_replika_connection_status(True, True)["state"], "stale")

    def test_rooms_agents_and_kindroid_routes_are_disabled_but_records_remain(self):
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/rooms").status_code, 404)
            self.assertEqual(client.get("/api/room").status_code, 404)
            self.assertEqual(client.get("/api/metrics").status_code, 404)
            self.assertEqual(client.post("/api/participants/local-model/generate", json={"prompt": "test"}).status_code, 404)
            self.assertEqual(client.post("/api/providers/kindroid/messages", json={}).status_code, 404)
            self.assertEqual(client.get("/api/providers/replika/browser-context").json(), {"bot_id": ""})
            self.assertFalse(client.get("/api/runtime").json()["browser_bridges_enabled"])

        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 5)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM provider_channels").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
