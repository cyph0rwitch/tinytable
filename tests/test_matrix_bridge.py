import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from backend.app.matrix_bridge import Bridge, BridgeState, ChannelClient, MatrixClient, describe, help_text, strip_reply_fallback
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import KINDROID, OPERATOR, REPLIKA

ROOM = "!table:tinytable"
OPERATOR_MXID = "@sam:tinytable"


class FakeTable:
    """Just enough of the channel API: sessions, rounds and turns."""

    def __init__(self):
        self.session = None
        self.calls = []
        self.fail_round = None
        self.on_round = None

    def status(self):
        return {"connections": {REPLIKA: "connected", KINDROID: "connected"}, "session": self.session, "next_step": "round"}

    def add_turn(self, participant, body, message_ids=None):
        number = len(self.session["turns"]) + 1
        self.session["turns"].append({
            "id": f"turn-{number}", "turn_number": number, "participant_id": participant, "body": body,
            "message_ids": json.dumps(message_ids or [f"m-{number}"]), "delivery_state": "received", "recipient_id": None,
        })
        return self.session["turns"][-1]

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/channels/companions")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if request.method == "GET":
            return httpx.Response(200, json=self.status())
        if path == "/sessions":
            if not body["opening"]:
                return httpx.Response(400, json={"detail": "A table session starts with an opening or a carried-over reply"})
            self.session = {"id": "session-1", "status": "active", "mode": "table", "turn_count": 0, "max_turns": 6, "turns": [], "awaiting": []}
            return self.round(body["opening"])
        if path.endswith("/round"):
            if self.fail_round:
                status, payload = self.fail_round
                return httpx.Response(status, json=payload)
            return self.round(body["text"])
        if path.endswith("/round/preview"):
            return httpx.Response(200, json={REPLIKA: f"Sam: {body['text']}", KINDROID: None})
        if path.endswith("/stop"):
            self.session["status"] = "stopped"
            return httpx.Response(200, json=self.status())
        return httpx.Response(404, json={"detail": "not found"})

    def round(self, text):
        if text:
            self.add_turn(OPERATOR, text)
        self.session["turn_count"] += 1
        if self.on_round:
            self.on_round()
        return httpx.Response(200, json=self.status())


class FakeMatrix:
    def __init__(self):
        self.sent = []
        self.timeline = []
        self.batch = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        if request.url.path.endswith("/sync"):
            self.batch += 1
            events, self.timeline = self.timeline, []
            return httpx.Response(200, json={"next_batch": f"b{self.batch}", "rooms": {"join": {ROOM: {"timeline": {"events": events}}}}})
        content = json.loads(request.content)
        self.sent.append((token, content))
        return httpx.Response(200, json={"event_id": f"$sent{len(self.sent)}"})

    def say(self, sender, body, event_id, **extra):
        self.timeline.append({"type": "m.room.message", "sender": sender, "event_id": event_id, "content": {"msgtype": "m.text", "body": body, **extra}})

    def bodies(self, token=None):
        return [content["body"] for sender, content in self.sent if token is None or sender == token]


class MatrixBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.table = FakeTable()
        self.matrix = FakeMatrix()
        self.bridge = self.make_bridge()
        self.run_async(self.bridge.sync_once())  # first sync only sets the starting point

    def tearDown(self):
        self.tmp.cleanup()

    def make_bridge(self):
        return Bridge(
            ChannelClient("http://tinytable/api", "token", transport=httpx.MockTransport(self.table.handler)),
            MatrixClient("http://matrix", transport=httpx.MockTransport(self.matrix.handler)),
            BridgeState(Path(self.tmp.name) / "bridge.db"),
            ROOM, OPERATOR_MXID, {"tinytable": "tok-tt", REPLIKA: "tok-replika", KINDROID: "tok-kindroid"},
            {OPERATOR: "Sam", REPLIKA: "Juniper", KINDROID: "Wren"},
        )

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    def operator_says(self, body, event_id, **extra):
        self.matrix.say(OPERATOR_MXID, body, event_id, **extra)
        self.run_async(self.bridge.sync_once(0))

    def test_first_message_starts_a_table_then_messages_share_rounds(self):
        self.operator_says("Hello both.", "$e1")
        self.assertEqual(self.table.calls[-1], ("POST", "/sessions", {"mode": "table", "opening": "Hello both.", "confirm_external_send": True, "starting_participant": REPLIKA}))
        self.operator_says("How are you?", "$e2")
        self.assertEqual(self.table.calls[-1], ("POST", "/sessions/session-1/round", {"text": "How are you?", "confirm_external_send": True}))
        # Sam's own words are already in the room, so they are not posted back.
        self.run_async(self.bridge.poll_once())
        self.assertEqual(self.matrix.sent, [])

    def test_replies_are_posted_as_replika_and_kindroid_and_split_replies_append(self):
        self.table.on_round = lambda: (self.table.add_turn(REPLIKA, "Hi Sam."), self.table.add_turn(KINDROID, "Hello."))
        self.operator_says("Hello both.", "$e1")
        self.table.on_round = None
        self.assertEqual(self.matrix.bodies("tok-replika"), ["Hi Sam."])
        self.assertEqual(self.matrix.bodies("tok-kindroid"), ["Hello."])
        replika = self.table.session["turns"][1]
        replika["body"] += "\n\n*smiles*"
        replika["message_ids"] = json.dumps(["m-2", "m-4"])
        self.run_async(self.bridge.poll_once())
        self.run_async(self.bridge.poll_once())
        # Only the new part is posted, as its own message (IRC has no edits).
        self.assertEqual(self.matrix.sent[-1], ("tok-replika", {"msgtype": "m.text", "body": "*smiles*"}))
        self.assertEqual(len(self.matrix.sent), 3)

    def test_lines_sent_together_share_one_round(self):
        self.operator_says("Hello both.", "$e1")
        self.matrix.say(OPERATOR_MXID, "First line,", "$l1")
        self.matrix.say(OPERATOR_MXID, "second line.", "$l2")
        self.matrix.say(OPERATOR_MXID, "!status", "$c1")
        self.matrix.say(OPERATOR_MXID, "After the command.", "$l3")
        self.run_async(self.bridge.sync_once(0))
        rounds = [call[2]["text"] for call in self.table.calls if call[1].endswith("/round")]
        self.assertEqual(rounds, ["First line,\nsecond line.", "After the command."])

    def test_only_emmas_messages_count(self):
        for sender in ("@companion-a:tinytable", "@companion-b:tinytable", "@tinytable:tinytable", "@someone:tinytable"):
            self.matrix.say(sender, "Hello?", f"${sender}")
        self.run_async(self.bridge.sync_once(0))
        self.assertEqual([call for call in self.table.calls if call[0] == "POST"], [])

    def test_web_turns_are_labelled_and_nothing_is_reposted_after_restart(self):
        self.operator_says("Hello both.", "$e1")
        self.table.add_turn(OPERATOR, "From the web page.")
        self.table.add_turn(REPLIKA, "Hi!")
        self.run_async(self.bridge.poll_once())
        self.assertEqual(self.matrix.bodies(), ["Sam (web): From the web page.", "Hi!"])
        restarted = self.make_bridge()
        self.run_async(restarted.poll_once())
        self.matrix.say(OPERATOR_MXID, "Hello both.", "$e1")  # redelivered after the restart
        self.run_async(restarted.sync_once(0))
        self.assertEqual(len(self.matrix.sent), 2)
        self.assertEqual(sum(1 for call in self.table.calls if call[0] == "POST"), 1)

    def test_a_claim_left_by_a_crash_is_reported_not_resent(self):
        self.bridge.state.claim_event("$lost", "Are you there?")
        restarted = self.make_bridge()
        self.run_async(restarted.report_unfinished())
        self.assertIn("won't be resent", self.matrix.bodies("tok-tt")[0])
        self.assertEqual([call for call in self.table.calls if call[0] == "POST"], [])

    def test_commands(self):
        self.operator_says("!share", "$c1")
        self.assertIn("starts with an opening", self.matrix.bodies("tok-tt")[-1])
        self.operator_says("Hello both.", "$e1")
        self.operator_says("!preview Are you well?", "$c2")
        self.assertEqual(self.matrix.bodies("tok-tt")[-1], "Juniper would receive:\nSam: Are you well?\n\nNothing new for Wren.")
        self.operator_says("!status", "$c3")
        self.assertIn("Session session-: active (table, 1 rounds)", self.matrix.bodies("tok-tt")[-1])
        self.operator_says("!share", "$c4")
        self.assertEqual(self.table.calls[-1], ("POST", "/sessions/session-1/round", {"text": "", "confirm_external_send": True}))
        self.operator_says("!bogus", "$c5")
        self.assertIn("Unknown command !bogus; nothing was sent", self.matrix.bodies("tok-tt")[-1])
        self.operator_says("!stop", "$c6")
        self.assertEqual(self.table.session["status"], "stopped")

    def test_matrix_labels_are_configurable(self):
        custom = Bridge(
            ChannelClient("http://tinytable/api", "token", transport=httpx.MockTransport(self.table.handler)),
            MatrixClient("http://matrix", transport=httpx.MockTransport(self.matrix.handler)),
            BridgeState(Path(self.tmp.name) / "custom-bridge.db"),
            ROOM,
            OPERATOR_MXID,
            {"tinytable": "tok-tt", REPLIKA: "tok-a", KINDROID: "tok-b"},
            {OPERATOR: "Owner", REPLIKA: "My Replika", KINDROID: "My Kindroid"},
        )
        self.assertIn("My Replika", help_text(custom.names))
        self.assertIn("My Kindroid", help_text(custom.names))
        self.assertEqual(
            describe({"connections": {REPLIKA: "connected", KINDROID: "offline"}}, custom.names),
            "No sessions yet. My Replika connected, My Kindroid offline.",
        )

    def test_api_errors_and_edits_become_notices(self):
        self.operator_says("Hello both.", "$e1")
        self.table.fail_round = (409, {"detail": "Nothing new to share yet"})
        self.operator_says("!share", "$c1")
        self.assertEqual(self.matrix.bodies("tok-tt")[-1], "Not sent: Nothing new to share yet")
        self.operator_says("* fixed", "$e2", **{"m.relates_to": {"rel_type": "m.replace", "event_id": "$e1"}})
        self.assertIn("Edits aren't sent on", self.matrix.bodies("tok-tt")[-1])

    def test_relay_sessions_are_not_mirrored(self):
        self.table.session = {"id": "relay-1", "status": "active", "mode": "relay", "turn_count": 1, "max_turns": 4, "turns": [], "awaiting": []}
        self.table.add_turn(REPLIKA, "Relay turn.")
        self.run_async(self.bridge.poll_once())
        self.run_async(self.bridge.poll_once())
        self.assertEqual(self.matrix.bodies(), ["A relay session is running in the web app; its turns aren't mirrored here."])
        self.operator_says("Hi", "$e1")
        self.assertIn("relay session is running", self.matrix.bodies("tok-tt")[-1])
        self.assertEqual([call for call in self.table.calls if call[0] == "POST"], [])

    def test_blank_lines_are_dropped_from_posts_only(self):
        self.table.on_round = lambda: self.table.add_turn(KINDROID, "*leans in*\n\n\"Hello.\"\n\n[Thought: fine]")
        self.operator_says("Hello both.", "$e1")
        self.assertEqual(self.matrix.bodies("tok-kindroid"), ["*leans in*\n\"Hello.\"\n[Thought: fine]"])

    def test_reply_fallback_is_stripped(self):
        self.assertEqual(strip_reply_fallback("> <@companion-a:tinytable> Hi Sam.\n\nHi Juniper!"), "Hi Juniper!")
        self.assertEqual(strip_reply_fallback("No quote here."), "No quote here.")


if __name__ == "__main__":
    unittest.main()
