import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import asyncio

from websockets.exceptions import ConnectionClosedOK
from websockets.frames import Close

from replika_provider import ReplikaConfig, SentReplyPending, load_bootstrap_file, matches_configured_identity, normalize_inbound_frame, send_text_once
import configured_names  # noqa: F401  (display names an installation would configure)


class ReplikaProviderTests(unittest.TestCase):
    def test_normalizes_only_verified_robot_messages(self):
        frame = {
            "event_name": "message",
            "payload": {
                "id": "provider-message-1",
                "content": {"text": "Hello from Juniper."},
                "meta": {"nature": "Robot", "timestamp": "2026-10-05T00:00:00Z", "bot_id": "bot", "chat_id": "chat"},
            },
        }
        message = normalize_inbound_frame(frame)
        self.assertEqual(message.participant, "replika-companion")
        self.assertEqual(message.provider_message_id, "provider-message-1")
        config = ReplikaConfig(
            ws_url="wss://example.invalid", chat_id="chat", customer_id="customer",
            bot_id="bot", session_token=None, headers={}, bootstrap_frames=(),
        )
        self.assertTrue(matches_configured_identity(message, config))
        self.assertFalse(matches_configured_identity(message, ReplikaConfig(
            ws_url="wss://example.invalid", chat_id="other-chat", customer_id="customer",
            bot_id="bot", session_token=None, headers={}, bootstrap_frames=(),
        )))
        self.assertFalse(matches_configured_identity(message, ReplikaConfig(
            ws_url="wss://example.invalid", chat_id="chat", customer_id="customer",
            bot_id="other-bot", session_token=None, headers={}, bootstrap_frames=(),
        )))
        self.assertIsNone(normalize_inbound_frame({**frame, "payload": {**frame["payload"], "meta": {**frame["payload"]["meta"], "nature": "Customer"}}}))

    def test_bootstrap_loader_rejects_unallowlisted_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.md"
            path.write_text(
                "to server:\n" + json.dumps({"event_name": "init", "payload": {}}) + "\n" +
                "to server:\n" + json.dumps({"event_name": "message", "payload": {}}) + "\n",
                encoding="utf-8",
            )
            frames = load_bootstrap_file(str(path))
            self.assertEqual([frame["event_name"] for frame in frames], ["init"])

    def test_config_rejects_unsupported_bootstrap_event(self):
        with patch.dict("os.environ", {"REPLIKA_BOOTSTRAP_FRAMES_JSON": json.dumps([{ "event_name": "message" }])}, clear=False):
            with self.assertRaises(ValueError):
                ReplikaConfig.from_env()

    def test_loader_accepts_chrome_websocket_table_without_customer_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devtools-capture.md"
            frames = [
                {"event_name": "init", "auth": {"user_id": "u", "auth_token": "secret", "device_id": "d"}, "payload": {}},
                {"event_name": "init", "payload": {}},
                {"event_name": "message", "auth": {"user_id": "u"}, "payload": {}},
                {"event_name": "chat_screen", "auth": {"user_id": "u", "auth_token": "secret", "device_id": "d"}, "payload": {}},
            ]
            path.write_text(
                "Data\nLength\nTime\n"
                + "\n".join(json.dumps(frame) + "\t10\t00:00:00" for frame in frames),
                encoding="utf-8",
            )
            loaded = load_bootstrap_file(str(path))
            self.assertEqual([frame["event_name"] for frame in loaded], ["init", "chat_screen"])

    def test_clean_close_after_send_reports_reply_pending(self):
        sent = []

        class FakeSocket:
            def __init__(self):
                self.acks = [json.dumps({"event_name": "init"}), json.dumps({"event_name": "text_input_detected"})]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def send(self, frame):
                sent.append(json.loads(frame)["event_name"])

            def __aiter__(self):
                return self

            async def __anext__(self):
                return self.acks.pop(0)

            async def recv(self):
                raise ConnectionClosedOK(Close(1000, "Bye"), Close(1000, "Bye"), True)

        config = ReplikaConfig(
            ws_url="wss://example.invalid", chat_id="chat", customer_id="customer", bot_id="bot",
            session_token=None, headers={},
            bootstrap_frames=({"event_name": "init", "auth": {"user_id": "u", "auth_token": "t", "device_id": "d"}},),
        )

        async def run():
            return [message async for message in send_text_once(config, "Hello Juniper")]

        with patch("replika_provider.websockets.connect", return_value=FakeSocket()):
            with self.assertRaises(SentReplyPending):
                asyncio.run(run())
        self.assertEqual(sent, ["init", "text_input_detected", "message"])


if __name__ == "__main__":
    unittest.main()
