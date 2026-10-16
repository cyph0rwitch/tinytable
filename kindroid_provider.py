#!/usr/bin/env python3
"""TinyTable's fail-closed Kindroid companion sidecar boundary.

The sidecar uses Kindroid's captured HTTP send endpoint and Firestore
Listen/channel long-poll receive path. Raw provider session material stays in
the mounted Secret; only hashed identity evidence crosses into TinyTable.
Malformed, unauthenticated, ambiguous, or unverified provider events remain
unavailable rather than being treated as delivered messages.

When KINDROID_API_KEY is set the sidecar uses Kindroid's account API key
instead of the captured browser session. The companion's reply arrives in the send
response and is ingested directly; the Firestore receiver is not started.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

try:
    from app import seats
    from app.kindroid_identity import canonical_identity_hash, identity_matches, normalize_identity_evidence
except ModuleNotFoundError:  # Local test invocation from the repository root.
    from backend.app import seats
    from backend.app.kindroid_identity import canonical_identity_hash, identity_matches, normalize_identity_evidence

KINDROID = seats.KINDROID
# Session files made before the seat rename name the older channel ID.
KINDROID_CHANNEL = seats.KINDROID_CHANNEL


def env_enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class SessionError(ValueError):
    """Raised when the mounted provider session is absent or unsafe to use."""


@dataclass(frozen=True)
class KindroidSession:
    """Raw provider session material held only in the mounted Secret."""

    provider: str
    channel_id: str
    api_base: str
    firestore_database: str
    account_id: str
    ai_id: str
    provider_channel_id: str
    speaker_id: str
    id_token: str
    client_platform: str
    firebase_api_key: str
    firebase_app_check: str
    firebase_gmpid: str
    goog_api_client: str

    @classmethod
    def from_file(cls, path: str) -> "KindroidSession":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError as exc:
            raise SessionError(f"{seats.name(KINDROID)} session file is not mounted") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"{seats.name(KINDROID)} session file is invalid") from exc
        if not isinstance(raw, dict):
            raise SessionError(f"{seats.name(KINDROID)} session must be a JSON object")

        def required(name: str) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value.strip():
                raise SessionError(f"{seats.name(KINDROID)} session field is missing: {name}")
            return value.strip()

        provider = required("provider").lower()
        channel_id = required("channel_id")
        if provider != "kindroid" or seats.canonical_channel(channel_id) != KINDROID_CHANNEL:
            raise SessionError(f"{seats.name(KINDROID)} session is bound to the wrong provider or channel")
        token = required("id_token")
        _reject_expired_jwt(token)
        return cls(
            provider=provider,
            channel_id=seats.canonical_channel(channel_id),
            api_base=str(raw.get("api_base") or "https://api.kindroid.ai").rstrip("/"),
            firestore_database=str(raw.get("firestore_database") or "projects/kindroid-ai/databases/(default)"),
            account_id=required("account_id"),
            ai_id=required("ai_id"),
            provider_channel_id=str(raw.get("provider_channel_id") or required("ai_id")),
            speaker_id=required("speaker_id"),
            id_token=token,
            client_platform=str(raw.get("client_platform") or "web"),
            firebase_api_key=required("firebase_api_key"),
            firebase_app_check=required("firebase_app_check"),
            firebase_gmpid=required("firebase_gmpid"),
            goog_api_client=required("goog_api_client"),
        )


@dataclass(frozen=True)
class KindroidApiIdentity:
    """Non-secret identity coordinates for API-key mode.

    The ID fields are read from the mounted session file without requiring
    its short-lived browser tokens to be valid.
    """

    api_base: str
    account_id: str
    ai_id: str
    provider_channel_id: str
    speaker_id: str

    @classmethod
    def from_file(cls, path: str) -> "KindroidApiIdentity":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError as exc:
            raise SessionError(f"{seats.name(KINDROID)} identity file is not mounted") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(f"{seats.name(KINDROID)} identity file is invalid") from exc
        if not isinstance(raw, dict):
            raise SessionError(f"{seats.name(KINDROID)} identity must be a JSON object")
        if str(raw.get("provider", "")).lower() != "kindroid" or seats.canonical_channel(str(raw.get("channel_id") or "")) != KINDROID_CHANNEL:
            raise SessionError(f"{seats.name(KINDROID)} identity is bound to the wrong provider or channel")

        def required(name: str) -> str:
            value = raw.get(name)
            if not isinstance(value, str) or not value.strip():
                raise SessionError(f"{seats.name(KINDROID)} identity field is missing: {name}")
            return value.strip()

        return cls(
            api_base=str(raw.get("api_base") or "https://api.kindroid.ai").rstrip("/"),
            account_id=required("account_id"),
            ai_id=required("ai_id"),
            provider_channel_id=str(raw.get("provider_channel_id") or required("ai_id")),
            speaker_id=required("speaker_id"),
        )


def _reject_expired_jwt(token: str) -> None:
    """Reject an expired JWT early; the provider remains authoritative."""

    pieces = token.split(".")
    if len(pieces) != 3:
        return
    try:
        padding = "=" * (-len(pieces[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(pieces[1] + padding).decode("utf-8"))
        expires_at = claims.get("exp")
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return
    if isinstance(expires_at, (int, float)) and expires_at <= time.time():
        raise SessionError(f"{seats.name(KINDROID)} session token is expired")


def session_identity_evidence(session: KindroidSession | KindroidApiIdentity, *, speaker_id: str | None = None) -> dict[str, str]:
    """Hash the three independent provider identity coordinates."""

    return {
        "account_hash": canonical_identity_hash(session.account_id),
        "channel_hash": canonical_identity_hash(session.provider_channel_id),
        "speaker_hash": canonical_identity_hash(speaker_id or session.speaker_id),
    }


@dataclass(frozen=True)
class KindroidConfig:
    tinytable_api: str
    listen_host: str
    listen_port: int
    channel_id: str
    account_hash: str
    channel_hash: str
    speaker_hash: str
    session_file: str
    protocol_ready: bool
    api_key: str = ""

    @classmethod
    def from_env(cls, *, tinytable_api: str | None = None, listen_port: int | None = None) -> "KindroidConfig":
        return cls(
            tinytable_api=(tinytable_api or os.getenv("TINYTABLE_API_URL", "http://127.0.0.1:8000/api")).rstrip("/"),
            listen_host=os.getenv("KINDROID_SIDECAR_HOST", "127.0.0.1"),
            listen_port=listen_port or int(os.getenv("KINDROID_SIDECAR_PORT", "8001")),
            channel_id=seats.canonical_channel(os.getenv("KINDROID_CHANNEL_ID", KINDROID_CHANNEL)),
            account_hash=os.getenv("KINDROID_ACCOUNT_HASH", "").strip().lower(),
            channel_hash=os.getenv("KINDROID_CHANNEL_HASH", "").strip().lower(),
            speaker_hash=os.getenv("KINDROID_SPEAKER_HASH", "").strip().lower(),
            session_file=os.getenv("KINDROID_SESSION_FILE", "/run/secrets/kindroid/session.json"),
            protocol_ready=env_enabled("KINDROID_PROTOCOL_READY", "0"),
            api_key=os.getenv("KINDROID_API_KEY", "").strip(),
        )

    @property
    def expected_identity(self) -> dict[str, str]:
        return {
            "account_hash": self.account_hash,
            "channel_hash": self.channel_hash,
            "speaker_hash": self.speaker_hash,
        }

    @property
    def identity_configured(self) -> bool:
        return all(re.fullmatch(r"[0-9a-f]{64}", value or "") for value in self.expected_identity.values())

    @property
    def session_configured(self) -> bool:
        return os.path.isfile(self.session_file)

    @property
    def api_mode(self) -> bool:
        return bool(self.api_key)

    def load_session(self) -> KindroidSession:
        return KindroidSession.from_file(self.session_file)


class KindroidAdapter:
    def __init__(self, config: KindroidConfig):
        self.config = config
        self._lock = threading.Lock()
        self._state = "starting"
        self._last_transition_at: str | None = None
        self._last_heartbeat_at: str | None = None
        self._received_provider_ids: set[str] = set()
        self._set_state(self._initial_state())

    def _initial_state(self) -> str:
        if not self.config.identity_configured or not self.config.session_configured:
            return "disconnected"
        if not self.config.protocol_ready:
            return "disconnected"
        if self.config.api_mode:
            # The API key has no side-effect-free check; a rejected send moves
            # the state to auth_expired.
            return "connected"
        return "starting"

    def _set_state(self, state: str) -> None:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._lock:
            if self._state != state or self._last_transition_at is None:
                self._last_transition_at = timestamp
            self._state = state
            self._last_heartbeat_at = timestamp

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "channel_id": self.config.channel_id,
                "last_transition_at": self._last_transition_at,
                "last_heartbeat_at": self._last_heartbeat_at,
                "identity_configured": self.config.identity_configured,
                "session_configured": self.config.session_configured,
                "protocol_ready": self.config.protocol_ready,
                "mode": "api_key" if self.config.api_mode else "browser_session",
            }

    def _require_ready(self) -> KindroidSession:
        if self.config.channel_id != KINDROID_CHANNEL:
            raise AdapterError(400, f"Only {KINDROID_CHANNEL} is enabled")
        if not self.config.identity_configured:
            self._set_state("identity_mismatch")
            raise AdapterError(503, f"{seats.name(KINDROID)} identity binding is not configured")
        if not self.config.session_configured:
            self._set_state("disconnected")
            raise AdapterError(503, f"{seats.name(KINDROID)} session file is not mounted")
        if not self.config.protocol_ready:
            self._set_state("disconnected")
            raise AdapterError(503, "Direct Kindroid protocol driver is not enabled")

        try:
            session = self.config.load_session()
        except SessionError as exc:
            detail = str(exc)
            self._set_state("auth_expired" if "expired" in detail.lower() else "disconnected")
            raise AdapterError(503, detail) from exc
        observed = session_identity_evidence(session)
        if not identity_matches(self.config.expected_identity, observed):
            self._set_state("identity_mismatch")
            raise AdapterError(403, f"{seats.name(KINDROID)} session identity does not match the configured binding")
        return session

    def _require_api_ready(self) -> KindroidApiIdentity:
        if self.config.channel_id != KINDROID_CHANNEL:
            raise AdapterError(400, f"Only {KINDROID_CHANNEL} is enabled")
        if not self.config.identity_configured:
            self._set_state("identity_mismatch")
            raise AdapterError(503, f"{seats.name(KINDROID)} identity binding is not configured")
        if not self.config.protocol_ready:
            self._set_state("disconnected")
            raise AdapterError(503, "Direct Kindroid protocol driver is not enabled")
        try:
            identity = KindroidApiIdentity.from_file(self.config.session_file)
        except SessionError as exc:
            self._set_state("disconnected")
            raise AdapterError(503, str(exc)) from exc
        if not identity_matches(self.config.expected_identity, session_identity_evidence(identity)):
            self._set_state("identity_mismatch")
            raise AdapterError(403, f"{seats.name(KINDROID)} identity does not match the configured binding")
        return identity

    def _send_with_api_key(self, text: str) -> dict[str, Any]:
        identity = self._require_api_ready()
        request_id = f"send-{uuid.uuid4()}"
        request = urllib.request.Request(
            f"{identity.api_base}/v1/send-message",
            data=json.dumps({"ai_id": identity.ai_id, "message": text}, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read(2_000_000).decode("utf-8", errors="replace")
                content_type = (response.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                self._set_state("auth_expired")
                raise AdapterError(503, "Kindroid API key was rejected") from exc
            if exc.code == 429:
                raise AdapterError(503, "Kindroid API rate limit reached") from exc
            if exc.code >= 500:
                # The provider may have delivered before failing.
                raise AdapterError(502, "Kindroid API failed after the request was sent") from exc
            raise AdapterError(exc.code, "Kindroid rejected the message") from exc
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, urllib.error.URLError) and not isinstance(exc.reason, TimeoutError):
                self._set_state("disconnected")
                raise AdapterError(503, "Kindroid API is unreachable") from exc
            # A timeout after the request was written is ambiguous; never retry.
            raise AdapterError(504, f"Timed out waiting for {seats.name(KINDROID)}'s reply") from exc
        self._set_state("connected")
        reply = api_reply_text(body, content_type)
        receipt = {
            "provider_message_id": None,
            "provider_message_id_authoritative": False,
            "provider_request_id": request_id,
            "delivery_status": "accepted_without_provider_id",
            "transport": "kindroid_api",
            "channel_id": self.config.channel_id,
            "reply_recorded": False,
        }
        if not reply:
            return receipt
        # Kindroid's API returns no message ID; this key is a local receipt
        # for deduplication and is labeled non-authoritative.
        try:
            stored = self._post_kindroid_message(
                reply,
                f"kindroid-api-reply:{request_id}",
                {"source_transport": "kindroid_api", "provider_message_id_authoritative": False, "reply_to_request_id": request_id},
                session_identity_evidence(identity),
            )
        except AdapterError as exc:
            # The Kindroid companion already received the message; report the local gap rather
            # than turning a delivered send into a failure.
            receipt["reply_error"] = exc.detail
            return receipt
        receipt["reply_recorded"] = True
        receipt["reply_message_id"] = stored.get("id")
        return receipt

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("confirm_external_send") is not True:
            raise AdapterError(400, f"Explicit confirmation is required before sending to {seats.name(KINDROID)}")
        text = str(payload.get("text") or "").strip()
        if not text:
            raise AdapterError(400, "Message cannot be empty")
        if self.config.api_mode:
            return self._send_with_api_key(text)
        session = self._require_ready()
        provider_payload = {
            "ai_id": session.ai_id,
            "stream": True,
            "message": text,
            "client_platform": session.client_platform,
            "idempotency_key": f"{session.ai_id}:{uuid.uuid4()}",
            "request_id": f"send-{uuid.uuid4()}",
        }
        request = urllib.request.Request(
            f"{session.api_base}/v1/send-message",
            data=json.dumps(provider_payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "text/event-stream, application/json",
                "Authorization": f"Bearer {session.id_token}",
                "Content-Type": "application/json",
                "Origin": "https://kindroid.ai",
                "Referer": "https://kindroid.ai/",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/event-stream" in content_type:
                    events = list(iter_sse_events(response))
                    provider_message_id = first_provider_message_id(events)
                else:
                    body = response.read().decode("utf-8", errors="replace")
                    provider_message_id = first_provider_message_id([parse_json_object(body)])
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                self._set_state("auth_expired")
                raise AdapterError(503, "Kindroid session was rejected") from exc
            if exc.code == 429 or exc.code >= 500:
                self._set_state("disconnected")
                raise AdapterError(503, "Kindroid send service is unavailable") from exc
            raise AdapterError(exc.code, "Kindroid rejected the message") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._set_state("disconnected")
            raise AdapterError(503, "Kindroid send request failed") from exc
        if not provider_message_id:
            # Kindroid can accept a streamed send with HTTP 200 while omitting
            # a message id from the SSE acknowledgement. Do not retry: the
            # provider may already have delivered it. The request id is a
            # local receipt key only and is never labeled authoritative.
            self._set_state("connected")
            return {
                "provider_message_id": None,
                "provider_message_id_authoritative": False,
                "provider_request_id": provider_payload["request_id"],
                "delivery_status": "accepted_without_provider_id",
                "transport": "kindroid_direct",
                "channel_id": self.config.channel_id,
            }
        self._set_state("connected")
        return {
            "provider_message_id": provider_message_id,
            "provider_message_id_authoritative": True,
            "provider_request_id": provider_payload["request_id"],
            "delivery_status": "accepted",
            "transport": "kindroid_direct",
            "channel_id": self.config.channel_id,
        }

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.config.api_mode:
            self._require_api_ready()
        else:
            self._require_ready()
        if seats.canonical_seat(str(payload.get("participant") or "")) != KINDROID:
            raise AdapterError(400, f"Kindroid sidecar accepts {seats.name(KINDROID)} messages only")
        if seats.canonical_channel(str(payload.get("channel_id") or "")) != self.config.channel_id or payload.get("channel_type") != "private":
            raise AdapterError(400, f"Invalid {seats.name(KINDROID)} private channel")
        text = str(payload.get("text") or "").strip()
        provider_message_id = str(payload.get("provider_message_id") or "").strip()
        if not text or not provider_message_id:
            raise AdapterError(400, "Provider message text and ID are required")
        identity_evidence = normalize_identity_evidence(payload.get("identity_evidence"))
        if not identity_matches(self.config.expected_identity, identity_evidence):
            self._set_state("identity_mismatch")
            raise AdapterError(403, f"{seats.name(KINDROID)} identity verification failed")
        metadata = payload.get("provider_metadata") if isinstance(payload.get("provider_metadata"), dict) else {}
        result = self._post_kindroid_message(text, provider_message_id, metadata, identity_evidence, str(payload.get("timestamp") or ""))
        self._set_state("connected")
        return result

    def _post_kindroid_message(self, text: str, provider_message_id: str, metadata: dict[str, Any], identity_evidence: dict[str, str], timestamp: str = "") -> dict[str, Any]:
        message = {
            "participant": KINDROID,
            "channel_id": self.config.channel_id,
            "channel_type": "private",
            "source": "kindroid",
            "transport": "kindroid_sidecar",
            "origin": "received_from_provider",
            "text": text,
            "provider_message_id": provider_message_id,
            "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider_metadata": metadata,
            "identity_evidence": identity_evidence,
        }
        return post_json(f"{self.config.tinytable_api}/providers/kindroid/messages", message)

    def start_receiver(self, stop_event: threading.Event) -> threading.Thread | None:
        if not env_enabled("TINYTABLE_KINDROID_RECEIVER_ENABLED") or not self.config.protocol_ready:
            return None
        if self.config.api_mode:
            # Firestore needs the browser session; API replies are ingested by send().
            return None
        thread = threading.Thread(target=self._receive_loop, args=(stop_event,), name="kindroid-receiver", daemon=True)
        thread.start()
        return thread

    def _receive_loop(self, stop_event: threading.Event) -> None:
        backoff = 1
        while not stop_event.is_set():
            try:
                session = self._require_ready()
                events = list(poll_kindroid_rest(session))
                self._set_state("connected")
                for event in events:
                    if stop_event.is_set():
                        return
                    normalized = normalize_firestore_event(event, session, self.config)
                    if normalized is None:
                        continue
                    provider_message_id = normalized["provider_message_id"]
                    if provider_message_id in self._received_provider_ids:
                        continue
                    try:
                        self.ingest(normalized)
                        self._received_provider_ids.add(provider_message_id)
                        if len(self._received_provider_ids) > 1000:
                            self._received_provider_ids = set(list(self._received_provider_ids)[-500:])
                    except AdapterError:
                        # Identity and malformed-message failures are fail-closed;
                        # polling remains alive for the next valid document.
                        continue
                backoff = 1
                stop_event.wait(5)
            except AdapterError as exc:
                if exc.status in {401, 403}:
                    self._set_state("auth_expired")
                else:
                    self._set_state("disconnected")
                stop_event.wait(15 if exc.status in {401, 403} else backoff)
                if exc.status not in {401, 403}:
                    backoff = min(backoff * 2, 30)
            except (OSError, ValueError, SessionError):
                self._set_state("disconnected")
                stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)


class AdapterError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def api_reply_text(body: str, content_type: str = "") -> str:
    """Extract the Kindroid companion's reply from a Kindroid API response body."""

    text = body.strip()
    if not text:
        return ""
    if "json" in content_type or text[:1] in {"{", "["}:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ("reply", "response", "message", "text", "content"):
                if isinstance(value.get(key), str) and value[key].strip():
                    return value[key].strip()
        return ""
    return text


def parse_json_object(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AdapterError(502, "Kindroid returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise AdapterError(502, "Kindroid returned an invalid response")
    return value


def iter_sse_events(stream: Any, *, max_bytes: int = 2_000_000):
    """Yield JSON SSE data blocks without logging provider content."""

    data_lines: list[str] = []
    consumed = 0
    for raw_line in stream:
        consumed += len(raw_line)
        if consumed > max_bytes:
            raise AdapterError(502, "Kindroid stream exceeded the safety limit")
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if data_lines:
                payload = parse_json_object("\n".join(data_lines))
                yield payload
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield parse_json_object("\n".join(data_lines))


def first_provider_message_id(events: list[dict[str, Any]]) -> str | None:
    preferred_keys = ("provider_message_id", "message_id", "messageId", "providerMessageId")

    def walk(value: Any, *, allow_generic_id: bool = False) -> str | None:
        if isinstance(value, dict):
            for key in preferred_keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            candidate = value.get("id")
            if isinstance(candidate, str) and candidate.strip() and allow_generic_id:
                return candidate.strip()
            for key, nested in value.items():
                if key in {"request_id", "idempotency_key"}:
                    continue
                found = walk(nested, allow_generic_id=key in {"message", "response", "content", "data"})
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = walk(nested, allow_generic_id=allow_generic_id)
                if found:
                    return found
        return None

    for event in events:
        found = walk(event)
        if found:
            return found
    return None


def _firestore_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "timestampValue" in value:
        return value["timestampValue"]
    if "referenceValue" in value:
        return value["referenceValue"]
    if "integerValue" in value:
        return value["integerValue"]
    if "doubleValue" in value:
        return value["doubleValue"]
    if "booleanValue" in value:
        return value["booleanValue"]
    if "nullValue" in value:
        return None
    if "mapValue" in value:
        fields = value["mapValue"].get("fields", {})
        return {key: _firestore_value(item) for key, item in fields.items()}
    if "arrayValue" in value:
        return [_firestore_value(item) for item in value["arrayValue"].get("values", [])]
    return {key: _firestore_value(item) for key, item in value.items()}


def _first_field(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in fields:
            return _firestore_value(fields[name])
    return None


def decrypt_kindroid_text(value: str, password: str) -> str | None:
    """Decrypt Kindroid's ``!enc:`` CryptoJS/OpenSSL envelope.

    Kindroid's web client calls ``CryptoJS.AES.decrypt(ciphertext, uid)`` for
    message fields. CryptoJS's password-based AES formatter emits a Base64
    ``Salted__`` envelope and derives the AES-256-CBC key/IV with the legacy
    MD5 EVP_BytesToKey-compatible KDF. A failed decode or padding check is
    deliberately rejected instead of exposing the ciphertext as chat text.
    """

    if not isinstance(value, str) or not value.startswith("!enc:"):
        return value if isinstance(value, str) else None
    if not isinstance(password, str) or not password:
        return None
    try:
        encoded = value[5:].strip()
        envelope = base64.b64decode(encoded, validate=True)
        if len(envelope) < 16 or envelope[:8] != b"Salted__":
            return None
        salt = envelope[8:16]
        ciphertext = envelope[16:]
        if not ciphertext or len(ciphertext) % 16:
            return None

        derived = b""
        previous = b""
        password_bytes = password.encode("utf-8")
        while len(derived) < 48:
            previous = hashlib.md5(previous + password_bytes + salt).digest()
            derived += previous
        decryptor = Cipher(
            algorithms.AES(derived[:32]),
            modes.CBC(derived[32:48]),
        ).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        padding = plaintext[-1]
        if not 1 <= padding <= 16 or plaintext[-padding:] != bytes([padding]) * padding:
            return None
        decoded = plaintext[:-padding].decode("utf-8")
        return decoded if decoded else None
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def normalize_firestore_event(event: dict[str, Any], session: KindroidSession, config: KindroidConfig) -> dict[str, Any] | None:
    change = event.get("documentChange")
    if not isinstance(change, dict):
        return None
    document = change.get("document")
    if not isinstance(document, dict):
        return None
    name = document.get("name")
    fields = document.get("fields")
    if not isinstance(name, str) or not isinstance(fields, dict):
        return None
    expected_prefix = f"{session.firestore_database}/documents/Users/{session.account_id}/AIs/{session.ai_id}/ChatMessages/"
    if not name.startswith(expected_prefix):
        return None
    provider_message_id = name.rsplit("/", 1)[-1]
    text = _first_field(fields, ("text", "message", "content"))
    if isinstance(text, dict):
        text = text.get("text") or text.get("body")
    speaker_id = _first_field(fields, ("speaker_id", "speakerId", "author_id", "authorId", "sender_id", "senderId", "speaker", "sender"))
    timestamp = _first_field(fields, ("timestamp", "created_at", "createdAt", "createTime"))
    if isinstance(text, str) and text.startswith("!enc:"):
        text = decrypt_kindroid_text(text, session.account_id)
    if not isinstance(text, str) or not text.strip() or not isinstance(speaker_id, str) or not speaker_id.strip():
        return None
    evidence = session_identity_evidence(session, speaker_id=speaker_id)
    if not identity_matches(config.expected_identity, evidence):
        return None
    return {
        "participant": KINDROID,
        "channel_id": config.channel_id,
        "channel_type": "private",
        "source": "kindroid",
        "transport": "kindroid_sidecar",
        "origin": "received_from_provider",
        "text": text.strip(),
        "provider_message_id": provider_message_id,
        "timestamp": str(timestamp or ""),
        "provider_metadata": {"firestore_document": name},
        "identity_evidence": evidence,
    }


def _firestore_listener_request(session: KindroidSession, *, method: str, query: dict[str, str], body: bytes | None = None) -> urllib.request.Request:
    url = "https://firestore.googleapis.com/google.firestore.v1.Firestore/Listen/channel?" + urllib.parse.urlencode(query)
    headers = {
        "Accept": "*/*",
        "Authorization": f"Bearer {session.id_token}",
        "Content-Type": "application/x-www-form-urlencoded" if body is not None else "",
        "Origin": "https://kindroid.ai",
        "Referer": "https://kindroid.ai/",
        "X-Firebase-AppCheck": session.firebase_app_check,
        "X-Firebase-GMPID": session.firebase_gmpid,
        "X-Goog-Api-Client": session.goog_api_client,
        "X-Goog-Api-Key": session.firebase_api_key,
    }
    headers = {key: value for key, value in headers.items() if value}
    return urllib.request.Request(url, data=body, headers=headers, method=method)


def _firestore_rest_request(session: KindroidSession) -> urllib.request.Request:
    path = "/v1/" + "/".join(
        (
            session.firestore_database,
            "documents",
            "Users",
            urllib.parse.quote(session.account_id, safe=""),
            "AIs",
            urllib.parse.quote(session.ai_id, safe=""),
            "ChatMessages",
        )
    )
    # Keep a bounded recovery window large enough to reconcile rows written by
    # an older sidecar build, while avoiding an unbounded transcript export.
    query = urllib.parse.urlencode({"pageSize": "100", "orderBy": "timestamp desc"})
    url = "https://firestore.googleapis.com" + path + "?" + query
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {session.id_token}",
        "Origin": "https://kindroid.ai",
        "Referer": "https://kindroid.ai/",
        "X-Firebase-AppCheck": session.firebase_app_check,
        "X-Firebase-GMPID": session.firebase_gmpid,
        "X-Goog-Api-Client": session.goog_api_client,
        "X-Goog-Api-Key": session.firebase_api_key,
    }
    return urllib.request.Request(url, headers=headers, method="GET")


def poll_kindroid_rest(session: KindroidSession) -> list[dict[str, Any]]:
    """Read the bounded latest-message window through Kindroid's HTTP API."""

    try:
        with urllib.request.urlopen(_firestore_rest_request(session), timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise AdapterError(401, "Kindroid message polling authentication was rejected") from exc
        if exc.code == 429 or exc.code >= 500:
            raise AdapterError(503, "Kindroid message polling is unavailable") from exc
        raise AdapterError(503, "Kindroid message polling failed") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise AdapterError(503, "Kindroid message polling failed") from exc

    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    if not isinstance(documents, list):
        return []
    return [
        {"documentChange": {"document": document}}
        for document in documents
        if isinstance(document, dict)
    ]


def _webchannel_payloads(body: bytes) -> list[dict[str, Any]]:
    """Decode the JSON array envelopes used by Firestore's HTTP channel."""

    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith(")]}'"):
        text = text[4:].lstrip("\r\n")
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    payloads: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if any(key in value for key in ("documentChange", "documentDelete", "targetChange", "filter")):
                payloads.append(value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    for candidate in candidates:
        walk(candidate)
    return payloads


def _webchannel_session_id(body: bytes) -> str | None:
    """Extract the SID from Firestore's length-prefixed channel handshake."""

    text = body.decode("utf-8", errors="replace").strip()
    if text.startswith(")]}'"):
        text = text[4:].lstrip("\r\n")
    values: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.isdigit():
            continue
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    def walk(value: Any) -> str | None:
        if isinstance(value, list):
            if len(value) >= 2 and value[0] == "c" and isinstance(value[1], str) and value[1].strip():
                return value[1].strip()
            for nested in value:
                found = walk(nested)
                if found:
                    return found
        elif isinstance(value, dict):
            for nested in value.values():
                found = walk(nested)
                if found:
                    return found
        return None

    for value in values:
        found = walk(value)
        if found:
            return found
    return None


def listen_kindroid(session: KindroidSession, stop_event: threading.Event):
    """Yield raw Firestore listen events until the stream closes."""

    database = session.firestore_database
    target_id = 1001
    request_counter = int(time.time() * 1000) % 1_000_000
    session_id = f"tt-{uuid.uuid4().hex}"
    query = {
        "database": database,
        "VER": "8",
        "RID": str(request_counter),
        "CVER": "22",
        "X-HTTP-Session-Id": "true",
        "zx": uuid.uuid4().hex,
        "t": "1",
    }
    target = {
        "database": database,
        "addTarget": {
            "query": {
                "structuredQuery": {
                    "from": [{"collectionId": "ChatMessages"}],
                    "orderBy": [
                        {"field": {"fieldPath": "timestamp"}, "direction": "DESCENDING"},
                        {"field": {"fieldPath": "__name__"}, "direction": "DESCENDING"},
                    ],
                    "limit": 20,
                },
                "parent": f"{database}/documents/Users/{session.account_id}/AIs/{session.ai_id}",
            },
            "targetId": target_id,
        },
    }
    channel_headers = "\n".join((
        f"X-Goog-Api-Client: {session.goog_api_client}",
        "Content-Type: application/json",
        f"X-Firebase-GMPID: {session.firebase_gmpid}",
        f"Authorization: Bearer {session.id_token}",
        f"x-firebase-appcheck: {session.firebase_app_check}",
        f"x-goog-api-key: {session.firebase_api_key}",
    ))
    body = urllib.parse.urlencode({
        "count": "1",
        "ofs": "0",
        "headers": channel_headers,
        "req0___data__": json.dumps(target, separators=(",", ":")),
    }).encode("utf-8")
    initial_request = _firestore_listener_request(session, method="POST", query=query, body=body)
    try:
        with urllib.request.urlopen(initial_request, timeout=30) as response:
            session_id = response.headers.get("X-HTTP-Session-Id") or session_id
            initial_body = response.read()
            sid = _webchannel_session_id(initial_body)
            if not sid:
                raise AdapterError(503, "Kindroid listener did not return a channel session")
            for event in _webchannel_payloads(initial_body):
                yield event
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise AdapterError(401, "Kindroid listener authentication was rejected") from exc
        raise AdapterError(503, "Kindroid listener setup failed") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AdapterError(503, "Kindroid listener connection failed") from exc

    while not stop_event.is_set():
        request_counter += 1
        get_query = {
            "database": database,
            "VER": "8",
            "RID": str(request_counter),
            "SID": sid,
            "AID": "0",
            "gsessionid": session_id,
            "zx": uuid.uuid4().hex,
            "t": "1",
        }
        request = _firestore_listener_request(session, method="GET", query=get_query)
        try:
            with urllib.request.urlopen(request, timeout=70) as response:
                for event in _webchannel_payloads(response.read()):
                    yield event
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise AdapterError(401, "Kindroid listener authentication was rejected") from exc
            raise AdapterError(503, "Kindroid listener request failed") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdapterError(503, "Kindroid listener connection failed") from exc


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, TypeError):
            pass
        raise AdapterError(exc.code, str(detail)) from exc
    except urllib.error.URLError as exc:
        raise AdapterError(503, "TinyTable API is unreachable") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AdapterError(502, "TinyTable API returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise AdapterError(502, "TinyTable API returned an invalid response")
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "TinyTableKindroidSidecar/0.1"

    @property
    def adapter(self) -> KindroidAdapter:
        return self.server.adapter  # type: ignore[attr-defined]

    def _write(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._write(200, {"ok": True, "service": "kindroid-sidecar", "status": self.adapter.status()})
            return
        if self.path == "/status":
            self._write(200, self.adapter.status())
            return
        self._write(404, {"detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            if self.path == "/send":
                result = self.adapter.send(payload)
            elif self.path == "/ingest":
                result = self.adapter.ingest(payload)
            else:
                self._write(404, {"detail": "Not found"})
                return
            self._write(200, result)
        except AdapterError as exc:
            self._write(exc.status, {"detail": exc.detail})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._write(400, {"detail": f"Invalid JSON request: {exc}"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def heartbeat_loop(adapter: KindroidAdapter, interval: int = 15) -> None:
    while True:
        state = adapter.status()["state"]
        try:
            post_json(
                f"{adapter.config.tinytable_api}/providers/kindroid/status",
                {"adapter": seats.KINDROID_ADAPTER, "state": state, "channel_id": adapter.config.channel_id},
            )
        except AdapterError:
            pass
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tinytable-api", default=None)
    parser.add_argument("--listen-port", type=int, default=None)
    parser.add_argument("--receive-only", action="store_true", help="Document the staged receive-only mode")
    args = parser.parse_args()
    config = KindroidConfig.from_env(tinytable_api=args.tinytable_api, listen_port=args.listen_port)
    adapter = KindroidAdapter(config)
    stop_event = threading.Event()
    adapter.start_receiver(stop_event)
    heartbeat = threading.Thread(target=heartbeat_loop, args=(adapter,), daemon=True)
    heartbeat.start()
    server = ThreadingHTTPServer((config.listen_host, config.listen_port), Handler)
    server.adapter = adapter  # type: ignore[attr-defined]
    server.serve_forever()


if __name__ == "__main__":
    main()
