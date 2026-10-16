"""Replika WebSocket adapter for TinyTable.

The normal receiver is strictly zero-send: it connects and reads only. The
separate explicit outbound prototype is kept behind ``--send-text`` and
``--enable-outbound``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from models import TinyTableMessage

try:
    from app import seats
except ModuleNotFoundError:  # Local invocation from the repository root.
    from backend.app import seats

LOGGER = logging.getLogger("tinytable.replika")
DEFAULT_WS_URL = "wss://ws.replika.ai/v17"
READ_ONLY_BOOTSTRAP_EVENTS = frozenset(
    {
        "init",
        "main_screen",
        "chat_screen",
        "application_started",
        "app_foreground",
        "history",
        "chat_suggestions_list",
    }
)


@dataclass(frozen=True)
class ReplikaConfig:
    """Connection values loaded from the environment.

    IDs and token are intentionally retained as configuration only. The
    read-only client does not put them into any outbound frame.
    """

    ws_url: str
    chat_id: str | None
    customer_id: str | None
    bot_id: str | None
    session_token: str | None
    headers: dict[str, str]
    bootstrap_frames: tuple[dict[str, Any], ...]

    @classmethod
    def from_env(cls) -> "ReplikaConfig":
        raw_headers = os.getenv("REPLIKA_WS_HEADERS_JSON", "{}").strip() or "{}"
        try:
            parsed_headers = json.loads(raw_headers)
        except json.JSONDecodeError as exc:
            raise ValueError("REPLIKA_WS_HEADERS_JSON must be valid JSON") from exc
        if not isinstance(parsed_headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed_headers.items()
        ):
            raise ValueError("REPLIKA_WS_HEADERS_JSON must be an object of string values")

        raw_bootstrap = os.getenv("REPLIKA_BOOTSTRAP_FRAMES_JSON", "[]").strip() or "[]"
        try:
            parsed_bootstrap = json.loads(raw_bootstrap)
        except json.JSONDecodeError as exc:
            raise ValueError("REPLIKA_BOOTSTRAP_FRAMES_JSON must be valid JSON") from exc
        if not isinstance(parsed_bootstrap, list) or not all(isinstance(frame, dict) for frame in parsed_bootstrap):
            raise ValueError("REPLIKA_BOOTSTRAP_FRAMES_JSON must be a JSON array of objects")
        for frame in parsed_bootstrap:
            event_name = frame.get("event_name")
            if event_name not in READ_ONLY_BOOTSTRAP_EVENTS:
                raise ValueError(
                    "Bootstrap contains an unsupported event; only explicitly observed "
                    f"read-only events are allowed: {event_name!r}"
                )

        return cls(
            ws_url=os.getenv("REPLIKA_WS_URL", DEFAULT_WS_URL),
            chat_id=os.getenv("REPLIKA_CHAT_ID") or None,
            customer_id=os.getenv("REPLIKA_CUSTOMER_ID") or None,
            bot_id=os.getenv("REPLIKA_BOT_ID") or None,
            session_token=os.getenv("REPLIKA_SESSION_TOKEN") or None,
            headers=parsed_headers,
            bootstrap_frames=tuple(parsed_bootstrap),
        )


def normalize_inbound_frame(frame: object) -> TinyTableMessage | None:
    """Return a TinyTable message only for a valid inbound Robot event.

    No fallback text, participant inference, IDs, timestamps, or tokens are
    generated here. Frames that do not carry the browser-observed Robot
    message fields are ignored.
    """

    if not isinstance(frame, Mapping):
        return None
    if frame.get("event_name") != "message":
        return None

    payload = frame.get("payload")
    if not isinstance(payload, Mapping):
        return None
    meta = payload.get("meta")
    content = payload.get("content")
    if not isinstance(meta, Mapping) or meta.get("nature") != "Robot":
        return None
    if not isinstance(content, Mapping):
        return None

    message_id = payload.get("id")
    timestamp = meta.get("timestamp")
    text = content.get("text")
    bot_id = meta.get("bot_id")
    chat_id = meta.get("chat_id")
    if not all(isinstance(value, str) and value for value in (message_id, timestamp, text, bot_id, chat_id)):
        LOGGER.warning("Ignoring Robot message with missing required fields")
        return None

    return TinyTableMessage(
        participant=seats.REPLIKA,
        source="replika",
        transport="websocket",
        origin="received_from_provider",
        text=text,
        provider_message_id=message_id,
        timestamp=timestamp,
        provider_metadata=dict(meta),
    )


def matches_configured_identity(message: TinyTableMessage, config: ReplikaConfig) -> bool:
    """Require the captured chat and configured bot to match before attributing to the Replika seat."""

    metadata = message.provider_metadata
    return bool(
        config.chat_id
        and config.bot_id
        and metadata.get("chat_id") == config.chat_id
        and metadata.get("bot_id") == config.bot_id
    )


class ReplikaReadOnlyProvider:
    """Receive and normalize frames from one Replika WebSocket session."""

    def __init__(self, config: ReplikaConfig, send_read_only_bootstrap: bool = False, status_sink: "TinyTableStatusSink | None" = None):
        self.config = config
        self.send_read_only_bootstrap = send_read_only_bootstrap
        self.status_sink = status_sink

    async def publish_status(self, state: str) -> None:
        if self.status_sink is not None:
            await self.status_sink.publish(state)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(15)
            await self.publish_status("connected")

    async def receive(self) -> AsyncIterator[TinyTableMessage]:
        retry_delay = 1.0
        try:
            while True:
                heartbeat_task = None
                try:
                    await self.publish_status("reconnecting")
                    LOGGER.info("Connecting to Replika WebSocket: %s", self.config.ws_url)
                    # Authentication headers, if needed, are supplied only as exact
                    # browser-observed values through the environment. This receive
                    async with websockets.connect(
                        self.config.ws_url,
                        extra_headers=self.config.headers,
                    ) as websocket:
                        LOGGER.info("Replika WebSocket connected")
                        retry_delay = 1.0
                        if self.send_read_only_bootstrap:
                            for frame in self.config.bootstrap_frames:
                                await websocket.send(json.dumps(frame, separators=(",", ":")))
                                LOGGER.info("Sent allowlisted read-only bootstrap frame event=%s", frame["event_name"])
                                await wait_for_event(websocket, frame["event_name"])
                        await self.publish_status("connected")
                        heartbeat_task = asyncio.create_task(self.heartbeat())
                        async for raw_frame in websocket:
                            LOGGER.debug("Received Replika frame (%d bytes)", len(raw_frame))
                            if not isinstance(raw_frame, str):
                                LOGGER.warning("Ignoring non-text Replika frame")
                                continue
                            try:
                                frame = json.loads(raw_frame)
                            except json.JSONDecodeError:
                                LOGGER.warning("Ignoring non-JSON Replika frame")
                                continue
                            message = normalize_inbound_frame(frame)
                            if message is not None:
                                if not matches_configured_identity(message, self.config):
                                    LOGGER.warning("Ignoring Robot message that failed configured chat/bot identity checks")
                                    continue
                                LOGGER.info("Received Robot message id=%s", message.provider_message_id)
                                yield message
                    LOGGER.info("Replika WebSocket session ended")
                except ConnectionClosed as exc:
                    LOGGER.info("Replika WebSocket closed code=%s reason=%s", exc.code, exc.reason)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Avoid logging exception text: handshake and transport errors can
                    # contain request details. Retry with a bounded exponential delay.
                    LOGGER.warning("Replika receiver error type=%s", type(exc).__name__)
                finally:
                    if heartbeat_task is not None:
                        heartbeat_task.cancel()
                        await asyncio.gather(heartbeat_task, return_exceptions=True)
                LOGGER.info("Reconnecting to Replika in %.0f seconds", retry_delay)
                await self.publish_status("reconnecting")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)
        finally:
            await self.publish_status("disconnected")


def bootstrap_auth(config: ReplikaConfig) -> dict[str, str]:
    """Extract the exact auth context observed in the bootstrap capture."""

    for frame in config.bootstrap_frames:
        auth = frame.get("auth")
        if isinstance(auth, Mapping) and all(isinstance(auth.get(key), str) and auth.get(key) for key in ("user_id", "auth_token", "device_id")):
            return {key: auth[key] for key in ("user_id", "auth_token", "device_id")}
    raise ValueError("Bootstrap frames contain no complete observed auth context")


def bootstrap_chat_id(config: ReplikaConfig) -> str:
    if config.chat_id:
        return config.chat_id
    for frame in config.bootstrap_frames:
        payload = frame.get("payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("chat_id"), str) and payload["chat_id"]:
            return payload["chat_id"]
    raise ValueError("No chat_id found in configuration or bootstrap frames")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def message_frame(config: ReplikaConfig, text: str) -> dict[str, Any]:
    """Build the observed browser-to-server customer frame.

    The server assigns the durable message ID; this frame deliberately has no
    client-created message ID. UUID fields follow two independent browser
    captures and remain behind the explicit outbound opt-in.
    """

    auth = bootstrap_auth(config)
    chat_id = bootstrap_chat_id(config)
    bot_id = config.bot_id
    if not bot_id:
        raise ValueError("REPLIKA_BOT_ID is required for outbound sending")
    return {
        "event_name": "message",
        "payload": {
            "content": {"type": "text", "text": text},
            "meta": {
                "bot_id": bot_id,
                "client_token": str(uuid.uuid4()).upper(),
                "chat_id": chat_id,
                "timestamp": timestamp(),
            },
        },
        "token": str(uuid.uuid4()),
        "auth": auth,
    }


async def wait_for_event(websocket: Any, expected_event: str, timeout_seconds: float = 15) -> dict[str, Any]:
    """Wait for the matching browser-observed protocol acknowledgement."""

    try:
        async with asyncio.timeout(timeout_seconds):
            async for raw_frame in websocket:
                if not isinstance(raw_frame, str):
                    continue
                try:
                    frame = json.loads(raw_frame)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, Mapping) and frame.get("event_name") == expected_event:
                    LOGGER.info("Received protocol acknowledgement event=%s", expected_event)
                    return dict(frame)
    except TimeoutError as exc:
        raise TimeoutError(f"Timed out waiting for protocol event {expected_event}") from exc
    raise ConnectionError(f"WebSocket closed before protocol event {expected_event}")


class SentReplyPending(Exception):
    """Replika cleanly closed the socket after the message frame was sent.

    Replika keeps one socket per session, so the sender can be closed when the
    receive-only sidecar reconnects. The reply then arrives through that
    receiver. This is reported as sent-without-reply, never as unsent.
    """


async def send_text_once(config: ReplikaConfig, text: str) -> AsyncIterator[TinyTableMessage]:
    """Send one explicitly requested customer message and yield Robot replies."""

    if not text.strip():
        raise ValueError("Outbound text cannot be empty")
    auth = bootstrap_auth(config)
    chat_id = bootstrap_chat_id(config)
    input_token = str(uuid.uuid4())
    input_frame = {
        "event_name": "text_input_detected",
        "payload": {"chat_id": chat_id},
        "token": input_token,
        "auth": auth,
    }
    async with websockets.connect(config.ws_url, extra_headers=config.headers) as websocket:
        LOGGER.info("Replika WebSocket connected for explicit outbound test")
        for frame in config.bootstrap_frames:
            await websocket.send(json.dumps(frame, separators=(",", ":")))
            LOGGER.info("Sent read-only bootstrap frame event=%s", frame["event_name"])
            await wait_for_event(websocket, frame["event_name"])
        await websocket.send(json.dumps(input_frame, separators=(",", ":")))
        LOGGER.info("Sent observed text_input_detected control frame")
        await wait_for_event(websocket, "text_input_detected")
        await websocket.send(json.dumps(message_frame(config, text), separators=(",", ":")))
        LOGGER.info("Sent one explicitly requested customer message")

        started = asyncio.get_running_loop().time()
        received_reply = False
        while True:
            elapsed = asyncio.get_running_loop().time() - started
            if elapsed >= 60:
                if received_reply:
                    return
                raise TimeoutError("Timed out waiting 60 seconds for Robot reply")
            wait_seconds = min(60 - elapsed, 10 if received_reply else 60)
            try:
                raw_frame = await asyncio.wait_for(websocket.recv(), timeout=wait_seconds)
            except asyncio.TimeoutError as exc:
                if received_reply:
                    return
                raise TimeoutError("Timed out waiting 60 seconds for Robot reply") from exc
            except ConnectionClosedOK as exc:
                if received_reply:
                    return
                LOGGER.info("Replika closed the sender cleanly after the message was sent")
                raise SentReplyPending() from exc
            if not isinstance(raw_frame, str):
                continue
            try:
                frame = json.loads(raw_frame)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, Mapping):
                LOGGER.info("Received Replika event=%s", frame.get("event_name", "unknown"))
            message = normalize_inbound_frame(frame)
            if message is not None:
                received_reply = True
                LOGGER.info("Received Robot reply id=%s", message.provider_message_id)
                yield message


def load_bootstrap_file(path: str) -> tuple[dict[str, Any], ...]:
    """Load exact read-only frames from a local browser capture.

    The preferred format labels ``to server:`` frames explicitly. Chrome's
    WebSocket inspector export instead writes one JSON frame followed by tab-
    separated length/time columns. For that format, authenticated frames are
    the browser-to-server direction; only the allowlisted initialization
    events are accepted. Customer-message frames remain rejected by the event
    allowlist.
    """

    frames: list[dict[str, Any]] = []
    direction: str | None = None
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    for line in raw_lines:
        candidate = line.strip()
        if candidate.lower() in {"to server:", "to server"}:
            direction = "to server"
            continue
        if candidate.lower() in {"to client:", "to client"}:
            direction = "to client"
            continue
        if not candidate.startswith("{"):
            continue
        if direction != "to server":
            continue
        try:
            frame = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict) or frame.get("event_name") not in READ_ONLY_BOOTSTRAP_EVENTS:
            continue
        frames.append(frame)
    if frames:
        return tuple(frames)

    # Chrome DevTools' WebSocket table has no direction labels. The browser's
    # outbound initialization frames carry the observed auth object; replies
    # do not. Parse only the JSON column and retain the same read-only event
    # allowlist used by the labeled format.
    for line in raw_lines:
        candidate = line.split("\t", 1)[0].strip()
        if not candidate.startswith("{"):
            continue
        try:
            frame = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict) or frame.get("event_name") not in READ_ONLY_BOOTSTRAP_EVENTS:
            continue
        if not isinstance(frame.get("auth"), Mapping):
            continue
        frames.append(frame)
    if not frames:
        raise ValueError(f"No allowed read-only bootstrap frames found in {path}")
    return tuple(frames)


class TinyTableSink:
    """Persist normalized inbound messages through TinyTable's local API."""

    def __init__(self, api_url: str, timeout: float = 10) -> None:
        self.url = api_url.rstrip("/") + "/providers/replika/messages"
        self.timeout = timeout

    async def persist(self, message: TinyTableMessage) -> None:
        payload = json.dumps(message.model_dump(mode="json")).encode("utf-8")

        def post() -> None:
            request = urllib.request.Request(
                self.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if response.status not in {200, 201}:
                        raise RuntimeError(f"TinyTable returned HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f"TinyTable returned HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise RuntimeError(f"TinyTable API request failed: {exc.reason}") from exc

        await asyncio.to_thread(post)
        LOGGER.info("Persisted provider message to TinyTable id=%s", message.provider_message_id)


class TinyTableStatusSink:
    """Publish non-sensitive receiver liveness to the local TinyTable API."""

    def __init__(self, api_url: str, timeout: float = 5) -> None:
        self.url = api_url.rstrip("/") + "/providers/replika/status"
        self.timeout = timeout

    async def publish(self, state: str) -> None:
        payload = json.dumps({"adapter": seats.REPLIKA_ADAPTER, "state": state}).encode("utf-8")

        def post() -> None:
            request = urllib.request.Request(
                self.url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if response.status not in {200, 201}:
                        raise RuntimeError(f"TinyTable returned HTTP {response.status}")
            except (urllib.error.HTTPError, urllib.error.URLError) as exc:
                raise RuntimeError(f"TinyTable status request failed: {type(exc).__name__}") from exc

        try:
            await asyncio.to_thread(post)
        except Exception as exc:
            LOGGER.warning("TinyTable status update failed type=%s", type(exc).__name__)


async def run(tinytable_api: str | None = None) -> None:
    config = ReplikaConfig.from_env()
    status_sink = TinyTableStatusSink(tinytable_api) if tinytable_api else None
    if status_sink is not None:
        await status_sink.publish("starting")
    provider = ReplikaReadOnlyProvider(config, status_sink=status_sink)
    sink = TinyTableSink(tinytable_api) if tinytable_api else None
    async for message in provider.receive():
        if sink is not None:
            await sink.persist(message)
        else:
            print(message.model_dump_json(), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Replika WebSocket receiver")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    parser.add_argument(
        "--bootstrap-file",
        help="load exact browser-observed bootstrap JSON frames from a local capture file",
    )
    parser.add_argument(
        "--tinytable-api",
        default=os.getenv("TINYTABLE_API_URL"),
        help="persist normalized messages to a TinyTable API base such as http://127.0.0.1:8000/api",
    )
    parser.add_argument("--send-text", help="send one customer message; requires --enable-outbound")
    parser.add_argument(
        "--enable-outbound",
        action="store_true",
        help="explicitly enable the one-message outbound prototype",
    )
    parser.add_argument(
        "--receive-only",
        action="store_true",
        help="force the normal receiver to use no bootstrap frames and send nothing",
    )
    parser.add_argument(
        "--receive-with-read-only-bootstrap",
        action="store_true",
        help="send only allowlisted observed initialization frames, never customer text",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.bootstrap_file:
            config = ReplikaConfig.from_env()
            config = replace(config, bootstrap_frames=load_bootstrap_file(args.bootstrap_file))
            if args.receive_only and not args.receive_with_read_only_bootstrap:
                config = replace(config, bootstrap_frames=())

            async def run_with_file() -> None:
                status_sink = TinyTableStatusSink(args.tinytable_api) if args.tinytable_api and args.send_text is None else None
                if status_sink is not None:
                    await status_sink.publish("starting")
                provider = ReplikaReadOnlyProvider(
                    config,
                    send_read_only_bootstrap=args.receive_with_read_only_bootstrap,
                    status_sink=status_sink,
                )
                sink = TinyTableSink(args.tinytable_api) if args.tinytable_api else None
                if args.send_text is not None:
                    if not args.enable_outbound:
                        raise ValueError("--send-text requires explicit --enable-outbound")
                    try:
                        async for message in send_text_once(config, args.send_text):
                            print(message.model_dump_json(), flush=True)
                            if sink is not None:
                                await sink.persist(message)
                    except SentReplyPending:
                        print(json.dumps({"origin": "sent_reply_pending"}), flush=True)
                    return
                async for message in provider.receive():
                    if sink is not None:
                        await sink.persist(message)
                    else:
                        print(message.model_dump_json(), flush=True)

            asyncio.run(run_with_file())
        else:
            if args.send_text is not None:
                raise ValueError("--send-text requires --bootstrap-file")
            if args.receive_only:
                config = ReplikaConfig.from_env()
                config = replace(config, bootstrap_frames=())

                async def run_receive_only() -> None:
                    status_sink = TinyTableStatusSink(args.tinytable_api) if args.tinytable_api else None
                    if status_sink is not None:
                        await status_sink.publish("starting")
                    provider = ReplikaReadOnlyProvider(config, send_read_only_bootstrap=False, status_sink=status_sink)
                    sink = TinyTableSink(args.tinytable_api) if args.tinytable_api else None
                    async for message in provider.receive():
                        if sink is not None:
                            await sink.persist(message)
                        else:
                            print(message.model_dump_json(), flush=True)

                asyncio.run(run_receive_only())
                return
            asyncio.run(run(args.tinytable_api))
    except KeyboardInterrupt:
        LOGGER.info("Stopped by operator")


if __name__ == "__main__":
    main()
