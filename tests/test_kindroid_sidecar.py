import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend.app.db as database
from backend.app.db import init_db
from backend.app.main import (
    KINDROID_CONNECTION_STATUS,
    KindroidMessageIn,
    KindroidStatusIn,
    app,
    current_kindroid_connection_status,
    kindroid_message,
    messages,
    participants,
    provider_channels,
    runtime,
    sync_kindroid_identity_binding,
    update_kindroid_status,
)
from backend.app.kindroid_identity import canonical_identity_hash
from fastapi import HTTPException
from fastapi.testclient import TestClient

from kindroid_provider import (
    KindroidAdapter,
    KindroidConfig,
    KindroidSession,
    api_reply_text,
    first_provider_message_id,
    iter_sse_events,
    normalize_firestore_event,
    poll_kindroid_rest,
    decrypt_kindroid_text,
    _webchannel_session_id,
)
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import KINDROID, KINDROID_CHANNEL, OPERATOR, REPLIKA


class KindroidSidecarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()
        self.original_status = dict(KINDROID_CONNECTION_STATUS)
        KINDROID_CONNECTION_STATUS.update({
            "state": "starting",
            "last_transition_at": None,
            "last_heartbeat_at": None,
            "channel_id": KINDROID_CHANNEL,
        })
        self.identity_values = {
            "account_id": "account-a",
            "provider_channel_id": "channel-b",
            "speaker_id": "speaker-c",
        }
        self.env = patch.dict(os.environ, {
            "TINYTABLE_COMPANIONS_ONLY": "1",
            "TINYTABLE_KINDROID_ENABLED": "1",
            "TINYTABLE_KINDROID_CHANNELS": KINDROID_CHANNEL,
            "TINYTABLE_KINDROID_RECEIVER_ENABLED": "1",
            "TINYTABLE_KINDROID_OUTBOUND_ENABLED": "1",
            "TINYTABLE_KINDROID_BRIDGE_ENABLED": "0",
            "KINDROID_ACCOUNT_HASH": canonical_identity_hash(self.identity_values["account_id"]),
            "KINDROID_CHANNEL_HASH": canonical_identity_hash(self.identity_values["provider_channel_id"]),
            "KINDROID_SPEAKER_HASH": canonical_identity_hash(self.identity_values["speaker_id"]),
        })
        self.env.start()
        sync_kindroid_identity_binding()

    def tearDown(self):
        self.env.stop()
        KINDROID_CONNECTION_STATUS.clear()
        KINDROID_CONNECTION_STATUS.update(self.original_status)
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def evidence(self):
        return {
            "account_hash": canonical_identity_hash(self.identity_values["account_id"]),
            "channel_hash": canonical_identity_hash(self.identity_values["provider_channel_id"]),
            "speaker_hash": canonical_identity_hash(self.identity_values["speaker_id"]),
        }

    def write_session(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({
                "provider": "kindroid",
                "channel_id": KINDROID_CHANNEL,
                "api_base": "https://api.kindroid.ai",
                "firestore_database": "projects/kindroid-ai/databases/(default)",
                "account_id": self.identity_values["account_id"],
                "ai_id": "ai-companion",
                "provider_channel_id": self.identity_values["provider_channel_id"],
                "speaker_id": self.identity_values["speaker_id"],
                "id_token": "test-token",
                "client_platform": "web",
                "firebase_api_key": "test-api-key",
                "firebase_app_check": "test-app-check",
                "firebase_gmpid": "test-gmpid",
                "goog_api_client": "test-client",
            }, handle)

    def message(self, **overrides):
        payload = {
            "participant": KINDROID,
            "channel_id": KINDROID_CHANNEL,
            "channel_type": "private",
            "source": "kindroid",
            "transport": "kindroid_sidecar",
            "origin": "received_from_provider",
            "text": "Hello from Wren.",
            "provider_message_id": "kindroid-1",
            "timestamp": "2026-10-09T04:00:00Z",
            "provider_metadata": {"fixture": True},
            "identity_evidence": self.evidence(),
        }
        payload.update(overrides)
        return KindroidMessageIn(**payload)

    def test_companions_only_mode_can_expose_only_kindroid_private_view(self):
        self.assertEqual([item["id"] for item in participants()], [OPERATOR, REPLIKA, KINDROID])
        self.assertEqual([item["id"] for item in provider_channels()], [KINDROID_CHANNEL])
        self.assertEqual(runtime()["mode"], "replika_plus_kindroid")

    def test_sidecar_message_requires_all_identity_digests_and_persists(self):
        saved = kindroid_message(self.message())
        self.assertEqual(saved["participant_id"], KINDROID)
        self.assertEqual(saved["raw_metadata"]["transport"], "kindroid_sidecar")
        self.assertEqual(messages(companion=KINDROID)[0]["participant_id"], KINDROID)

        with self.assertRaisesRegex(HTTPException, "identity verification failed"):
            kindroid_message(self.message(provider_message_id="kindroid-2", identity_evidence={"account_hash": "a" * 64}))
        self.assertEqual(len(messages(companion=KINDROID)), 1)

    def test_sidecar_reconciles_an_older_encrypted_projection(self):
        encrypted = self.message(
            provider_message_id="kindroid-encrypted",
            text="!enc:U2FsdGVkX18AESIzRFVmd3Iho6xABxTwHKcwHrjpDjr06BF0EEnd+Ib3uawvvbsI",
        )
        kindroid_message(encrypted)
        repaired = kindroid_message(encrypted.model_copy(update={"text": "Hello from Wren."}))
        self.assertEqual(repaired["body"], "Hello from Wren.")
        self.assertEqual(repaired["raw_metadata"]["decrypted_by"], "kindroid_sidecar")

    def test_browser_bridge_is_opt_in(self):
        with TestClient(app) as client:
            response = client.post("/api/providers/kindroid/messages", json=self.message().model_dump() | {"transport": "dom_observer"})
        self.assertEqual(response.status_code, 403)

    def test_status_reports_connected_then_stale(self):
        update_kindroid_status(KindroidStatusIn(state="connected"))
        self.assertEqual(current_kindroid_connection_status(True, True)["state"], "connected")
        KINDROID_CONNECTION_STATUS["last_heartbeat_at"] = "2000-01-01T00:00:00+00:00"
        self.assertEqual(current_kindroid_connection_status(True, True)["state"], "stale")

    def test_sidecar_send_fails_closed_until_protocol_driver_is_enabled(self):
        with tempfile.NamedTemporaryFile() as session:
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash="a" * 64,
                channel_hash="b" * 64,
                speaker_hash="c" * 64,
                session_file=session.name,
                protocol_ready=False,
            )
            adapter = KindroidAdapter(config)
            with self.assertRaisesRegex(Exception, "protocol driver is not enabled"):
                adapter.send({"text": "hello", "confirm_external_send": True})

    def test_sidecar_ingest_forwards_normalized_event_after_identity_check(self):
        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash=self.evidence()["account_hash"],
                channel_hash=self.evidence()["channel_hash"],
                speaker_hash=self.evidence()["speaker_hash"],
                session_file=session.name,
                protocol_ready=True,
            )
            adapter = KindroidAdapter(config)
            with patch("kindroid_provider.post_json", return_value={"id": "stored"}) as forward:
                result = adapter.ingest({
                    "participant": KINDROID,
                    "channel_id": KINDROID_CHANNEL,
                    "channel_type": "private",
                    "text": "A verified message.",
                    "provider_message_id": "provider-1",
                    "timestamp": "2026-10-09T04:00:00Z",
                    "identity_evidence": self.evidence(),
                })
            self.assertEqual(result, {"id": "stored"})
            forwarded = forward.call_args.args[1]
            self.assertEqual(forwarded["transport"], "kindroid_sidecar")
            self.assertEqual(forwarded["identity_evidence"], self.evidence())

    def api_adapter(self, session_path):
        return KindroidAdapter(KindroidConfig(
            tinytable_api="http://127.0.0.1:9/api",
            listen_host="127.0.0.1",
            listen_port=18001,
            channel_id=KINDROID_CHANNEL,
            account_hash=self.evidence()["account_hash"],
            channel_hash=self.evidence()["channel_hash"],
            speaker_hash=self.evidence()["speaker_hash"],
            session_file=session_path,
            protocol_ready=True,
            api_key="kn_test",
        ))

    def test_api_key_send_ingests_kindroid_reply_with_identity_evidence(self):
        class FakeResponse:
            headers = {"Content-Type": "text/plain"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit=-1):
                return b"Hi Juniper, lovely to meet you."

        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            adapter = self.api_adapter(session.name)
            self.assertEqual(adapter.status()["state"], "connected")
            self.assertEqual(adapter.status()["mode"], "api_key")
            self.assertIsNone(adapter.start_receiver(__import__("threading").Event()))
            with patch("kindroid_provider.urllib.request.urlopen", return_value=FakeResponse()) as request, \
                    patch("kindroid_provider.post_json", return_value={"id": "stored"}) as forward:
                result = adapter.send({"text": "hello", "confirm_external_send": True})
        sent = request.call_args.args[0]
        self.assertTrue(sent.full_url.endswith("/v1/send-message"))
        self.assertEqual(sent.headers["Authorization"], "Bearer kn_test")
        self.assertEqual(json.loads(sent.data), {"ai_id": "ai-companion", "message": "hello"})
        self.assertTrue(result["reply_recorded"])
        self.assertFalse(result["provider_message_id_authoritative"])
        forwarded = forward.call_args.args[1]
        self.assertEqual(forwarded["text"], "Hi Juniper, lovely to meet you.")
        self.assertEqual(forwarded["identity_evidence"], self.evidence())
        self.assertEqual(forwarded["provider_metadata"]["source_transport"], "kindroid_api")
        self.assertTrue(forwarded["provider_message_id"].startswith("kindroid-api-reply:send-"))

    def test_api_key_rejection_and_timeout_fail_without_retry(self):
        import urllib.error

        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            adapter = self.api_adapter(session.name)
            rejected = urllib.error.HTTPError("https://api.kindroid.ai/v1/send-message", 401, "Unauthorized", {}, io.BytesIO(b""))
            with patch("kindroid_provider.urllib.request.urlopen", side_effect=rejected) as request:
                with self.assertRaises(Exception) as caught:
                    adapter.send({"text": "hello", "confirm_external_send": True})
            self.assertEqual(caught.exception.status, 503)
            self.assertEqual(adapter.status()["state"], "auth_expired")
            self.assertEqual(request.call_count, 1)
            with patch("kindroid_provider.urllib.request.urlopen", side_effect=TimeoutError()) as request:
                with self.assertRaises(Exception) as caught:
                    adapter.send({"text": "hello", "confirm_external_send": True})
            self.assertEqual(caught.exception.status, 504)
            self.assertEqual(request.call_count, 1)

    def test_api_reply_text_accepts_plain_and_json_bodies(self):
        self.assertEqual(api_reply_text("Hello."), "Hello.")
        self.assertEqual(api_reply_text('"Hello."', "application/json"), "Hello.")
        self.assertEqual(api_reply_text('{"reply": "Hello."}', "application/json"), "Hello.")
        self.assertEqual(api_reply_text('{"ok": true}', "application/json"), "")

    def test_session_identity_is_reloaded_from_mounted_file(self):
        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            loaded = KindroidSession.from_file(session.name)
            self.assertEqual(loaded.ai_id, "ai-companion")
            self.assertEqual(loaded.channel_id, KINDROID_CHANNEL)

    def test_kindroid_cryptojs_envelope_decrypts_with_account_id(self):
        envelope = "!enc:U2FsdGVkX18AESIzRFVmd3Iho6xABxTwHKcwHrjpDjr06BF0EEnd+Ib3uawvvbsI"
        self.assertEqual(decrypt_kindroid_text(envelope, "account-a"), "Hello from Wren.")
        self.assertIsNone(decrypt_kindroid_text(envelope, "wrong-account"))

    def test_firestore_normalizer_decrypts_encrypted_message_text(self):
        with tempfile.NamedTemporaryFile() as session_file:
            self.write_session(session_file.name)
            session = KindroidSession.from_file(session_file.name)
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash=self.evidence()["account_hash"],
                channel_hash=self.evidence()["channel_hash"],
                speaker_hash=self.evidence()["speaker_hash"],
                session_file=session_file.name,
                protocol_ready=True,
            )
            event = {
                "documentChange": {
                    "document": {
                        "name": "projects/kindroid-ai/databases/(default)/documents/Users/account-a/AIs/ai-companion/ChatMessages/provider-enc",
                        "fields": {
                            "message": {"stringValue": "!enc:U2FsdGVkX18AESIzRFVmd3Iho6xABxTwHKcwHrjpDjr06BF0EEnd+Ib3uawvvbsI"},
                            "sender": {"stringValue": "speaker-c"},
                            "timestamp": {"integerValue": "1791518400000"},
                        },
                    }
                }
            }
            normalized = normalize_firestore_event(event, session, config)
            self.assertIsNotNone(normalized)
            self.assertEqual(normalized["text"], "Hello from Wren.")

    def test_send_stream_returns_authoritative_provider_id(self):
        class FakeResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter([b'data: {"message_id":"provider-42"}\n', b"\n"])

        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash=self.evidence()["account_hash"],
                channel_hash=self.evidence()["channel_hash"],
                speaker_hash=self.evidence()["speaker_hash"],
                session_file=session.name,
                protocol_ready=True,
            )
            adapter = KindroidAdapter(config)
            with patch("kindroid_provider.urllib.request.urlopen", return_value=FakeResponse()) as request:
                result = adapter.send({"text": "hello", "confirm_external_send": True})
            self.assertEqual(result["provider_message_id"], "provider-42")
            sent = json.loads(request.call_args.args[0].data.decode("utf-8"))
            self.assertEqual(sent["ai_id"], "ai-companion")
            self.assertTrue(sent["stream"])
            self.assertTrue(sent["request_id"].startswith("send-"))
            self.assertTrue(sent["idempotency_key"].startswith("ai-companion:"))
            self.assertIn("Authorization", request.call_args.args[0].headers)

    def test_send_stream_accepts_without_provider_id_without_retrying(self):
        class FakeResponse:
            headers = {"Content-Type": "text/event-stream"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter([b'data: {"status":"accepted"}\n', b"\n"])

        with tempfile.NamedTemporaryFile() as session:
            self.write_session(session.name)
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash=self.evidence()["account_hash"],
                channel_hash=self.evidence()["channel_hash"],
                speaker_hash=self.evidence()["speaker_hash"],
                session_file=session.name,
                protocol_ready=True,
            )
            adapter = KindroidAdapter(config)
            with patch("kindroid_provider.urllib.request.urlopen", return_value=FakeResponse()) as request:
                result = adapter.send({"text": "hello", "confirm_external_send": True})
            self.assertIsNone(result["provider_message_id"])
            self.assertFalse(result["provider_message_id_authoritative"])
            self.assertEqual(result["delivery_status"], "accepted_without_provider_id")
            self.assertTrue(result["provider_request_id"].startswith("send-"))
            self.assertEqual(adapter.status()["state"], "connected")
            self.assertEqual(request.call_count, 1)

    def test_firestore_document_requires_expected_speaker_identity(self):
        with tempfile.NamedTemporaryFile() as session_file:
            self.write_session(session_file.name)
            session = KindroidSession.from_file(session_file.name)
            config = KindroidConfig(
                tinytable_api="http://127.0.0.1:9/api",
                listen_host="127.0.0.1",
                listen_port=18001,
                channel_id=KINDROID_CHANNEL,
                account_hash=self.evidence()["account_hash"],
                channel_hash=self.evidence()["channel_hash"],
                speaker_hash=self.evidence()["speaker_hash"],
                session_file=session_file.name,
                protocol_ready=True,
            )
            event = {
                "documentChange": {
                    "document": {
                        "name": "projects/kindroid-ai/databases/(default)/documents/Users/account-a/AIs/ai-companion/ChatMessages/provider-42",
                        "fields": {
                            "message": {"stringValue": "A verified reply."},
                            "sender": {"stringValue": "speaker-c"},
                            "timestamp": {"integerValue": "1791518400000"},
                        },
                    }
                }
            }
            normalized = normalize_firestore_event(event, session, config)
            self.assertEqual(normalized["provider_message_id"], "provider-42")
            self.assertEqual(normalized["identity_evidence"], self.evidence())
            event["documentChange"]["document"]["fields"]["sender"] = {"stringValue": "wrong"}
            self.assertIsNone(normalize_firestore_event(event, session, config))

    def test_sse_parser_discards_keepalive_and_preserves_json(self):
        events = list(iter_sse_events(io.BytesIO(b": keepalive\n\ndata: {\"message\":{\"id\":\"x\"}}\n\n")))
        self.assertEqual(first_provider_message_id(events), "x")

    def test_firestore_channel_handshake_extracts_sid_from_length_frame(self):
        body = b'51\n[[0,["c","sid-from-kindroid","",8,15,30000]]]\n'
        self.assertEqual(_webchannel_session_id(body), "sid-from-kindroid")

    def test_firestore_rest_poll_normalizes_document_envelopes(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"documents": [{"name": "doc-1", "fields": {"message": {"stringValue": "reply"}}}]}).encode()

        with tempfile.NamedTemporaryFile() as session_file:
            self.write_session(session_file.name)
            session = KindroidSession.from_file(session_file.name)
            with patch("kindroid_provider.urllib.request.urlopen", return_value=FakeResponse()) as request:
                events = poll_kindroid_rest(session)
            self.assertEqual(events[0]["documentChange"]["document"]["name"], "doc-1")
            self.assertIn("pageSize=100", request.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
