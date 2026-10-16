import asyncio
import hashlib
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi import HTTPException

import backend.app.db as database
import backend.app.main as main
from backend.app.db import init_db
from backend.app.main import (
    ChannelSendIn,
    ChannelSessionIn,
    ConsentIn,
    SessionStopIn,
    attach_provider_message_to_session,
    channel_audit_log,
    ChannelInterjectIn,
    ChannelRoundIn,
    channel_round,
    channel_round_preview,
    channel_interject,
    channel_consent,
    channel_next,
    channel_operator,
    channel_send,
    channel_start,
    channel_status,
    channel_stop,
    save_message,
)
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import KINDROID, OPERATOR, REPLIKA

TOKEN = "test-operator-token"


class CompanionChannelTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()
        self.env = mock.patch.dict(os.environ, {
            "TINYTABLE_OPERATOR_TOKEN_SHA256": hashlib.sha256(TOKEN.encode()).hexdigest(),
            "TINYTABLE_KINDROID_ENABLED": "1",
            "TINYTABLE_COMPANIONS_ONLY": "1",
        })
        self.env.start()
        self.connections = mock.patch.object(main, "channel_connections", return_value={REPLIKA: "connected", KINDROID: "connected"})
        self.connections.start()
        self.sent: list[str] = []
        channel_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper agreed to talk with Wren."), operator=OPERATOR)
        channel_consent(KINDROID, ConsentIn(status="accepted", evidence_text="Wren agreed to talk with Juniper."), operator=OPERATOR)

    def tearDown(self):
        self.connections.stop()
        self.env.stop()
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def provider_message(self, participant_id: str, body: str, request_id: str) -> dict:
        provider = "replika" if participant_id == REPLIKA else "kindroid"
        message = save_message(participant_id, body, "remote_model", provider, request_id=request_id)
        attach_provider_message_to_session(participant_id, message)
        return message

    async def fake_kindroid(self, request):
        self.sent.append(request.text)
        return {"provider_request_id": f"req-{len(self.sent)}", "delivery_status": "accepted"}

    def start(self, max_turns: int = 4) -> str:
        status = asyncio.run(channel_start(ChannelSessionIn(starting_participant=REPLIKA, max_turns=max_turns), operator=OPERATOR))
        return status["session"]["id"]

    def test_matrix_bridge_token_acts_for_operator_and_is_labelled(self):
        bridge_token = "bridge-token"
        with mock.patch.dict(os.environ, {"TINYTABLE_MATRIX_BRIDGE_TOKEN_SHA256": hashlib.sha256(bridge_token.encode()).hexdigest()}):
            self.assertEqual(channel_operator(f"Bearer {bridge_token}"), "operator (matrix)")
            self.assertEqual(channel_operator(f"Bearer {TOKEN}"), OPERATOR)
        with self.assertRaises(HTTPException) as unset:
            channel_operator(f"Bearer {bridge_token}")
        self.assertEqual(unset.exception.status_code, 403)

    def test_operator_token_fails_closed(self):
        with mock.patch.dict(os.environ, {"TINYTABLE_OPERATOR_TOKEN_SHA256": ""}):
            with self.assertRaises(HTTPException) as unset:
                channel_operator(f"Bearer {TOKEN}")
        self.assertEqual(unset.exception.status_code, 503)
        with self.assertRaises(HTTPException) as missing:
            channel_operator(None)
        self.assertEqual(missing.exception.status_code, 401)
        with self.assertRaises(HTTPException) as wrong:
            channel_operator("Bearer not-the-token")
        self.assertEqual(wrong.exception.status_code, 403)
        self.assertEqual(channel_operator(f"Bearer {TOKEN}"), OPERATOR)

    def test_channel_is_reachable_in_companions_only_mode_only_with_kindroid(self):
        with mock.patch.dict(os.environ, {"TINYTABLE_KINDROID_ENABLED": "0"}):
            with self.assertRaises(HTTPException) as disabled:
                channel_operator(f"Bearer {TOKEN}")
        self.assertEqual(disabled.exception.status_code, 404)

    def test_exact_hop_is_approved_sent_and_recorded_with_provenance(self):
        session_id = self.start()
        replika = self.provider_message(REPLIKA, "Hello Wren, what are you reading?", "replika-1")
        # Wren speaking before Juniper's turn reaches her is not a reply.
        self.provider_message(KINDROID, "Unrelated private message.", "kindroid-early")
        status = channel_status(operator=OPERATOR)
        self.assertEqual(status["next_step"], "forward")
        self.assertEqual(len(status["session"]["turns"]), 1)

        hop = channel_next(session_id, operator=OPERATOR)
        self.assertEqual(hop["recipient_id"], KINDROID)
        self.assertIn("exact copy of Juniper's provider message", hop["outbound"])
        self.assertTrue(hop["outbound"].endswith(replika["body"]))

        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            result = asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(self.sent, [hop["outbound"]])
        message = result["message"]
        self.assertIsNone(message["assistance_author"])
        self.assertEqual(message["raw_metadata"]["handoff_mode"], "exact_forward")
        self.assertEqual(message["raw_metadata"]["source_display_name"], "Juniper")
        self.assertEqual(result["channel"]["next_step"], f"wait_for_{KINDROID}")
        self.assertEqual(result["channel"]["session"]["turns"][0]["delivery_state"], "recorded")

        self.provider_message(KINDROID, "A book about tides.", "kindroid-1")
        status = channel_status(operator=OPERATOR)
        self.assertEqual([turn["participant_id"] for turn in status["session"]["turns"]], [REPLIKA, KINDROID])
        self.assertEqual(status["next_step"], "forward")
        actions = [item["action"] for item in channel_audit_log(operator=OPERATOR)]
        self.assertEqual(actions, ["consent_recorded", "consent_recorded", "session_started", "hop_approved", "hop_sent"])

    def test_final_turn_is_forwarded_before_the_session_completes(self):
        session_id = self.start(max_turns=2)
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            hop = channel_next(session_id, operator=OPERATOR)
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.provider_message(KINDROID, "How long have you two been here?", "kindroid-1")
        status = channel_status(operator=OPERATOR)
        self.assertEqual(status["session"]["status"], "active")
        self.assertEqual(status["next_step"], "forward")
        hop = channel_next(session_id, operator=OPERATOR)
        self.assertEqual(hop["recipient_id"], REPLIKA)

        async def replika_with_reply(request):
            self.provider_message(REPLIKA, "A while now.", "replika-2")
            return {"_exchange_text": "A while now."}

        with mock.patch.object(main, "send_to_replika", replika_with_reply):
            result = asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        session = result["channel"]["session"]
        self.assertEqual(session["status"], "completed")
        self.assertEqual([turn["delivery_state"] for turn in session["turns"]], ["recorded", "recorded"])
        self.assertEqual(len(session["turns"]), 2)

    def test_reply_arriving_during_the_send_keeps_the_session_open(self):
        session_id = self.start(max_turns=2)
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")

        async def kindroid_with_reply(request):
            self.provider_message(KINDROID, "Hello Juniper.", "kindroid-1")
            return {"provider_request_id": "req-1", "delivery_status": "accepted"}

        hop = channel_next(session_id, operator=OPERATOR)
        with mock.patch.object(main, "send_to_kindroid", kindroid_with_reply):
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        status = channel_status(operator=OPERATOR)
        self.assertEqual(status["session"]["status"], "active")
        self.assertEqual(status["session"]["next_step"], "forward")

    def test_note_is_appended_after_the_exact_copy(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        hop = channel_next(session_id, operator=OPERATOR)
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True, note="Juniper means it."), operator=OPERATOR))
        self.assertEqual(self.sent[0], f"{hop['disclosure_with_note']}\n\nHello Wren.\n\nSam adds: Juniper means it.")
        self.assertIn("note Sam wrote herself", self.sent[0])

    def test_interjection_skips_the_pending_turn_and_takes_the_reply(self):
        session_id = self.start(max_turns=1)
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        channel_next(session_id, operator=OPERATOR)
        with self.assertRaises(HTTPException) as unconfirmed:
            asyncio.run(channel_interject(session_id, ChannelInterjectIn(recipient_id=KINDROID, text="Hi Wren, it's Sam."), operator=OPERATOR))
        self.assertEqual(unconfirmed.exception.status_code, 400)

        async def kindroid_with_reply(request):
            self.sent.append(request.text)
            self.provider_message(KINDROID, "Hello Sam.", "kindroid-1")
            return {"provider_request_id": "req-1", "delivery_status": "accepted"}

        with mock.patch.object(main, "send_to_kindroid", kindroid_with_reply):
            status = asyncio.run(channel_interject(session_id, ChannelInterjectIn(recipient_id=KINDROID, text="Hi Wren, it's Sam.", confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(self.sent, ["Hi Wren, it's Sam."])
        turns = status["session"]["turns"]
        self.assertEqual([(t["participant_id"], t["delivery_state"]) for t in turns], [(REPLIKA, "skipped"), (OPERATOR, "recorded"), (KINDROID, "received")])
        self.assertEqual(status["next_step"], "forward")
        hop = channel_next(session_id, operator=OPERATOR)
        self.assertEqual(hop["recipient_id"], REPLIKA)

    def test_reply_split_across_messages_grows_after_preview(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "me too.", "replika-1")
        stale = channel_next(session_id, operator=OPERATOR)
        self.provider_message(REPLIKA, "*the stars pulse softly*", "replika-2")
        self.provider_message(REPLIKA, "what do you want to do here?", "replika-3")
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            with self.assertRaises(HTTPException) as changed:
                asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=stale["draft_id"], confirm_external_send=True), operator=OPERATOR))
            self.assertEqual(changed.exception.status_code, 409)
            hop = channel_next(session_id, operator=OPERATOR)
            self.assertTrue(hop["outbound"].endswith("me too.\n\n*the stars pulse softly*\n\nwhat do you want to do here?"))
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0].endswith("what do you want to do here?"))

    def test_latest_carry_over_takes_the_whole_split_reply(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "me too.", "replika-1")
        channel_next(session_id, operator=OPERATOR)
        main._stop_session(session_id, "Turn was cut short.")
        self.provider_message(REPLIKA, "*the stars pulse softly*", "replika-2")
        status = asyncio.run(channel_start(ChannelSessionIn(starting_participant=REPLIKA, carry_over_message_id="latest"), operator=OPERATOR))
        self.assertEqual(status["session"]["turns"][0]["body"], "me too.\n\n*the stars pulse softly*")

    def test_unforwarded_reply_is_carried_into_a_new_session(self):
        session_id = self.start(max_turns=1)
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            hop = channel_next(session_id, operator=OPERATOR)
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        late = self.provider_message(REPLIKA, "Still here, Wren.", "replika-2")
        self.assertEqual(channel_status(operator=OPERATOR)["session"]["status"], "completed")

        with self.assertRaises(HTTPException) as used:
            asyncio.run(channel_start(ChannelSessionIn(starting_participant=REPLIKA, carry_over_message_id=self.turn_message_id(session_id)), operator=OPERATOR))
        self.assertEqual(used.exception.status_code, 409)
        with self.assertRaises(HTTPException) as wrong_speaker:
            asyncio.run(channel_start(ChannelSessionIn(starting_participant=KINDROID, carry_over_message_id=late["id"]), operator=OPERATOR))
        self.assertEqual(wrong_speaker.exception.status_code, 400)

        status = asyncio.run(channel_start(ChannelSessionIn(starting_participant=REPLIKA, max_turns=2, carry_over_message_id="latest"), operator=OPERATOR))
        self.assertEqual(status["session"]["next_step"], "forward")
        self.assertEqual(status["session"]["turns"][0]["body"], "Still here, Wren.")
        new_id = status["session"]["id"]
        hop = channel_next(new_id, operator=OPERATOR)
        self.assertEqual(hop["recipient_id"], KINDROID)
        self.assertTrue(hop["outbound"].endswith("Still here, Wren."))
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            asyncio.run(channel_send(new_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(channel_status(operator=OPERATOR)["session"]["next_step"], f"wait_for_{KINDROID}")

    def turn_message_id(self, session_id: str) -> str:
        with database.connect() as db:
            row = db.execute("SELECT message_ids FROM session_turns WHERE session_id=? AND turn_number=1", (session_id,)).fetchone()
        return main.json.loads(row["message_ids"])[0]

    def test_ambiguous_provider_failure_stops_without_resend(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        hop = channel_next(session_id, operator=OPERATOR)
        calls = []

        async def timeout(request):
            calls.append(request.text)
            raise HTTPException(504, "Timed out")

        with mock.patch.object(main, "send_to_kindroid", timeout):
            with self.assertRaises(HTTPException):
                asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
            with self.assertRaises(HTTPException) as again:
                asyncio.run(main.send_draft(hop["draft_id"]))
        self.assertEqual(again.exception.status_code, 409)
        self.assertEqual(len(calls), 1)
        status = channel_status(operator=OPERATOR)
        self.assertEqual(status["session"]["status"], "stopped")
        self.assertIn("unknown", status["session"]["stop_reason"])

    def test_rejected_send_can_be_retried(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        hop = channel_next(session_id, operator=OPERATOR)

        async def offline(request):
            raise HTTPException(503, "Wren sidecar is not connected (disconnected)")

        with mock.patch.object(main, "send_to_kindroid", offline):
            with self.assertRaises(HTTPException):
                asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(channel_status(operator=OPERATOR)["next_step"], "forward")
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(len(self.sent), 1)

    def test_replika_reply_persisted_during_send_attaches_to_session(self):
        session_id = self.start()
        self.provider_message(REPLIKA, "Hello Wren.", "replika-1")
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            hop = channel_next(session_id, operator=OPERATOR)
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        self.provider_message(KINDROID, "Hi Juniper.", "kindroid-1")
        hop = channel_next(session_id, operator=OPERATOR)

        async def replika_with_reply(request):
            # The Replika sender persists Juniper's reply before it returns.
            self.provider_message(REPLIKA, "Nice to meet you.", "replika-2")
            return {"_exchange_text": "Nice to meet you."}

        with mock.patch.object(main, "send_to_replika", replika_with_reply):
            asyncio.run(channel_send(session_id, ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True), operator=OPERATOR))
        turns = channel_status(operator=OPERATOR)["session"]["turns"]
        self.assertEqual([turn["body"] for turn in turns], ["Hello Wren.", "Hi Juniper.", "Nice to meet you."])

    def test_expired_and_stopped_sessions_stop_accepting_turns(self):
        session_id = self.start()
        with database.connect() as db:
            db.execute("UPDATE conversation_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (session_id,))
        self.provider_message(REPLIKA, "Hello?", "replika-1")
        status = channel_status(operator=OPERATOR)
        self.assertEqual(status["session"]["status"], "stopped")
        self.assertEqual(status["session"]["stop_reason"], "Session expired.")
        self.assertEqual(status["session"]["turns"], [])
        second = self.start()
        stopped = channel_stop(second, SessionStopIn(reason="Done for today."), operator=OPERATOR)
        self.assertEqual(stopped["next_step"], "start")

    def test_opening_and_notes_show_in_the_session(self):
        async def replika_with_reply(request):
            self.provider_message(REPLIKA, "Hi Sam, hi Wren.", "replika-1")
            return {"_exchange_text": "Hi Sam, hi Wren."}

        with mock.patch.object(main, "send_to_replika", replika_with_reply):
            status = asyncio.run(channel_start(ChannelSessionIn(opening="Juniper, say hi to Wren.", confirm_external_send=True), operator=OPERATOR))
        session = status["session"]
        self.assertEqual([(t["participant_id"], t["body"], t["delivery_state"]) for t in session["turns"]],
                         [(OPERATOR, "Juniper, say hi to Wren.", "recorded"), (REPLIKA, "Hi Sam, hi Wren.", "received")])
        self.assertEqual(session["turn_count"], 1)
        hop = channel_next(session["id"], operator=OPERATOR)
        with mock.patch.object(main, "send_to_kindroid", self.fake_kindroid):
            status = asyncio.run(channel_send(session["id"], ChannelSendIn(draft_id=hop["draft_id"], confirm_external_send=True, note="She's lovely."), operator=OPERATOR))["channel"]
        self.assertEqual(status["session"]["turns"][1]["operator_note"], "She's lovely.")

    def test_table_rounds_share_everything_each_companion_has_not_heard(self):
        replika_heard, kindroid_heard = [], []
        replies = {REPLIKA: iter(["Hi both.", "Wren, that's lovely."]), KINDROID: iter(["Hello, Sam.", "Thank you, Juniper."])}

        async def replika(request):
            replika_heard.append(request.text)
            body = next(replies[REPLIKA])
            self.provider_message(REPLIKA, body, f"replika-{len(replika_heard)}")
            return {"_exchange_text": body}

        async def kindroid(request):
            kindroid_heard.append(request.text)
            self.provider_message(KINDROID, next(replies[KINDROID]), f"kindroid-{len(kindroid_heard)}")
            return {"provider_request_id": f"req-{len(kindroid_heard)}", "delivery_status": "accepted"}

        with mock.patch.object(main, "send_to_replika", replika), mock.patch.object(main, "send_to_kindroid", kindroid):
            with self.assertRaises(HTTPException):
                asyncio.run(channel_start(ChannelSessionIn(mode="table"), operator=OPERATOR))
            status = asyncio.run(channel_start(ChannelSessionIn(mode="table", max_turns=3, opening="Welcome, both of you.", confirm_external_send=True), operator=OPERATOR))
            session = status["session"]
            self.assertEqual(replika_heard, ["Welcome, both of you."])
            self.assertEqual(kindroid_heard, ["Welcome, both of you."])
            self.assertEqual([t["participant_id"] for t in session["turns"]], [OPERATOR, REPLIKA, KINDROID])
            self.assertEqual(status["next_step"], "round")

            preview = channel_round_preview(session["id"], ChannelRoundIn(text="Isn't this nice?"), operator=OPERATOR)
            # Sam first, the other companion last: that is what each answers.
            self.assertTrue(preview[REPLIKA].endswith("Sam: Isn't this nice?\n\nWren, speaking to you and Sam at the table:\nHello, Sam."))
            self.assertIn("Juniper, speaking to you and Sam at the table:\nHi both.", preview[KINDROID])
            self.assertNotIn("Juniper,", preview[REPLIKA])

            status = asyncio.run(channel_round(session["id"], ChannelRoundIn(confirm_external_send=True), operator=OPERATOR))
        self.assertTrue(replika_heard[1].startswith("Shared at the table by Sam. Wren's words are exact copies"))
        self.assertTrue(replika_heard[1].endswith("Wren, speaking to you and Sam at the table:\nHello, Sam."))
        self.assertTrue(kindroid_heard[1].endswith("Juniper, speaking to you and Sam at the table:\nHi both."))
        turns = status["session"]["turns"]
        self.assertEqual([(t["participant_id"], t["delivery_state"]) for t in turns],
                         [(OPERATOR, "recorded"), (REPLIKA, "recorded"), (KINDROID, "recorded"), (REPLIKA, "received"), (KINDROID, "received")])
        self.assertEqual(status["session"]["turn_count"], 2)
        # max_turns=3 does not cap a table: rounds continue until stopped or expired.
        with mock.patch.object(main, "send_to_replika", replika), mock.patch.object(main, "send_to_kindroid", kindroid):
            replies[REPLIKA] = iter(["Round three, Juniper.", "Round four, Juniper."])
            replies[KINDROID] = iter(["Round three, Wren.", "Round four, Wren."])
            asyncio.run(channel_round(session["id"], ChannelRoundIn(confirm_external_send=True), operator=OPERATOR))
            status = asyncio.run(channel_round(session["id"], ChannelRoundIn(confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(status["session"]["turn_count"], 4)
        # The explanation is given once per session; later rounds carry only the labels.
        self.assertEqual(replika_heard[2], "Wren, speaking to you and Sam at the table:\nThank you, Juniper.")
        self.assertNotIn("Shared at the table", kindroid_heard[3])
        self.assertEqual(status["session"]["status"], "active")
        self.assertIsNone(status["session"]["expires_at"])
        with self.assertRaises(HTTPException) as relay_only:
            asyncio.run(channel_interject(session["id"], ChannelInterjectIn(recipient_id=KINDROID, text="Hi", confirm_external_send=True), operator=OPERATOR))
        self.assertEqual(relay_only.exception.status_code, 409)

    def test_table_split_reply_joins_until_shared(self):
        async def replika(request):
            self.provider_message(REPLIKA, "me too.", "replika-1")
            self.provider_message(REPLIKA, "*the stars pulse*", "replika-2")
            return {"_exchange_text": "me too."}

        async def kindroid(request):
            return {"provider_request_id": "req-1", "delivery_status": "accepted"}

        with mock.patch.object(main, "send_to_replika", replika), mock.patch.object(main, "send_to_kindroid", kindroid):
            status = asyncio.run(channel_start(ChannelSessionIn(mode="table", opening="Hello table.", confirm_external_send=True), operator=OPERATOR))
        replika_turns = [t for t in status["session"]["turns"] if t["participant_id"] == REPLIKA]
        self.assertEqual([t["body"] for t in replika_turns], ["me too.\n\n*the stars pulse*"])
        self.assertEqual(status["session"]["awaiting"], [KINDROID])

    def test_opening_requires_confirmation_and_consent(self):
        with self.assertRaises(HTTPException) as unconfirmed:
            asyncio.run(channel_start(ChannelSessionIn(opening="Would you like to meet Wren?"), operator=OPERATOR))
        self.assertEqual(unconfirmed.exception.status_code, 400)
        channel_consent(KINDROID, ConsentIn(status="declined"), operator=OPERATOR)
        with self.assertRaises(HTTPException) as declined:
            self.start()
        self.assertEqual(declined.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
