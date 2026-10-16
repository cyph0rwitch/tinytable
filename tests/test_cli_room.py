import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import backend.app.db as database
from backend.app.cli_room import ESTATE_ROOM, post, relay, room_messages, roster, send, send_all
from backend.app.db import connect, init_db
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import OPERATOR


AGENTS = json.dumps([
    {"id": "alpha", "name": "Alpha", "transport": "hermes_local", "profile": "default"},
    {"id": "beta", "name": "Beta", "transport": "hermes_local", "profile": "beta"},
    {"id": "gamma", "name": "Gamma", "transport": "hermes_peer", "peer": "peer-host"},
])


class CliRoomTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"TINYTABLE_CLI_AGENTS": AGENTS})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_configured_agents_are_seeded_with_their_transports(self):
        items = {item["id"]: item for item in roster()}
        self.assertEqual(set(items), {"alpha", "beta", "gamma"})
        self.assertEqual(items["alpha"]["transport"], "hermes_local")
        self.assertEqual(items["beta"]["target"], "beta")
        self.assertEqual(items["gamma"]["transport"], "hermes_peer")

    def test_post_stays_local_until_explicit_send(self):
        message = post("Hello estate")
        self.assertEqual(message["room_id"], ESTATE_ROOM)
        self.assertEqual(message["participant_id"], OPERATOR)
        self.assertEqual(len(room_messages()), 1)
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0], 0)

    def test_codex_relay_preserves_agent_author_and_transport(self):
        message = relay("gamma", "Gamma's attributed response")
        self.assertEqual(message["participant_id"], "gamma")
        self.assertEqual(message["provider"], "codex-relay")
        self.assertEqual(message["raw_metadata"]["transport"], "codex_relay")
        self.assertEqual(room_messages()[0]["participant_id"], "gamma")

    @patch("backend.app.cli_room.subprocess.run")
    def test_send_records_response_and_transport_provenance(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["hermes"], returncode=0, stdout="Gamma reply", stderr=""
        )
        message = post("Check in, Gamma")
        result = send(message["id"], "gamma")
        self.assertEqual(result["state"], "delivered")
        self.assertEqual(result["message"]["participant_id"], "gamma")
        self.assertEqual(result["message"]["provider"], "hermes")
        self.assertEqual(run.call_args.args[0][:3], ["hermes", "peer", "dm"])
        with connect() as db:
            attempt = db.execute("SELECT * FROM delivery_attempts").fetchone()
            self.assertEqual(attempt["state"], "delivered")
            self.assertEqual(attempt["external_id"], result["message"]["id"])

    @patch("backend.app.cli_room.subprocess.run")
    def test_failed_and_timed_out_sends_are_recorded_without_agent_messages(self, run):
        message = post("Please report status")
        run.return_value = subprocess.CompletedProcess(args=["hermes"], returncode=2, stdout="", stderr="bad")
        failed = send(message["id"], "beta")
        self.assertEqual(failed["state"], "failed")
        run.side_effect = subprocess.TimeoutExpired(["hermes"], 1)
        timed_out = send(message["id"], "alpha", timeout=1)
        self.assertEqual(timed_out["state"], "timeout")
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE participant_id != 'operator'").fetchone()[0], 0)
            states = [row[0] for row in db.execute("SELECT state FROM delivery_attempts ORDER BY created_at")]
        self.assertEqual(states, ["failed", "timeout"])

    @patch("backend.app.cli_room.subprocess.run")
    def test_explicit_broadcast_creates_one_attempt_per_agent(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["hermes"], returncode=0, stdout="Agent reply", stderr=""
        )
        message = post("Hello everyone")
        result = send_all(message["id"])
        self.assertEqual(set(result), {"alpha", "beta", "gamma"})
        self.assertTrue(all(item["state"] == "delivered" for item in result.values()))
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE participant_id != 'operator'").fetchone()[0], 3)

    @patch("backend.app.cli_room.subprocess.run")
    def test_broadcast_accepts_text_and_posts_it_once(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["hermes"], returncode=0, stdout="Echo reply", stderr=""
        )
        result = send_all("Test echo back 121")
        self.assertTrue(all(item["posted"] for item in result.values()))
        with connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE participant_id='operator'").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery_attempts").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
