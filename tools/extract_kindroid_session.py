#!/usr/bin/env python3
"""Derive a private Kindroid session from a Chromium HAR.

The HAR is treated as sensitive input.  This utility never prints token or
identity values; it writes the session and digest files with mode 0600.
The provider's ``sender=ai`` role is used as the companion's speaker identity;
incoming documents must still prove that same identity at runtime or they are
dropped. The provider AI id remains the channel coordinate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterator


DOCUMENT_RE = re.compile(
    r"(?P<database>projects/[^/]+/databases/[^/]+)/documents/Users/"
    r"(?P<account>[^/]+)/AIs/(?P<ai>[^/]+)(?:/|$)"
)


def walk(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)
    elif isinstance(value, str):
        yield value


def form_json(value: str) -> Any | None:
    try:
        return json.loads(urllib.parse.unquote_plus(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("har", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    raw = json.loads(args.har.read_text(encoding="utf-8"))
    entries = raw.get("log", {}).get("entries", [])
    if not isinstance(entries, list):
        raise SystemExit("HAR has no entries")

    headers: dict[str, str] = {}
    database = account_id = ai_id = None
    api_base = "https://api.kindroid.ai"
    client_platform = "web"

    for entry in entries:
        request = entry.get("request", {})
        url = str(request.get("url") or "")
        body = str(request.get("postData", {}).get("text") or "")

        if "firestore.googleapis.com" in url:
            form = urllib.parse.parse_qs(body, keep_blank_values=True)
            if "headers" in form and not headers:
                header_text = urllib.parse.unquote_plus(form["headers"][0])
                for line in header_text.splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
            for values in form.values():
                for encoded in values:
                    decoded = form_json(encoded)
                    for text in walk(decoded):
                        match = DOCUMENT_RE.search(text)
                        if match:
                            database = database or match.group("database")
                            account_id = account_id or match.group("account")
                            ai_id = ai_id or match.group("ai")

        if "/v1/send-message" in url:
            api_base = url.split("/v1/send-message", 1)[0].rstrip("/")
            try:
                send_payload = json.loads(body)
            except (TypeError, ValueError, json.JSONDecodeError):
                send_payload = {}
            ai_id = ai_id or send_payload.get("ai_id")
            client_platform = str(send_payload.get("client_platform") or client_platform)

    token = headers.get("authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    required = {
        "database": database,
        "account_id": account_id,
        "ai_id": ai_id,
        "id_token": token,
        "firebase_api_key": headers.get("x-goog-api-key", ""),
        "firebase_app_check": headers.get("x-firebase-appcheck", ""),
        "firebase_gmpid": headers.get("x-firebase-gmpid", ""),
        "goog_api_client": headers.get("x-goog-api-client", ""),
    }
    missing = [key for key, value in required.items() if not isinstance(value, str) or not value.strip()]
    if missing:
        raise SystemExit("HAR is missing required session material: " + ", ".join(missing))

    # Kindroid's Firestore documents identify the provider side as sender=ai.
    # A provider event with another sender will not pass the configured digest
    # at runtime. The AI id remains independently bound as the channel id.
    speaker_id = "ai"
    session = {
        "provider": "kindroid",
        "channel_id": "kindroid:companion",
        "api_base": api_base,
        "firestore_database": database,
        "account_id": account_id,
        "ai_id": ai_id,
        "provider_channel_id": ai_id,
        "speaker_id": speaker_id,
        "id_token": token,
        "client_platform": client_platform,
        "firebase_api_key": required["firebase_api_key"],
        "firebase_app_check": required["firebase_app_check"],
        "firebase_gmpid": required["firebase_gmpid"],
        "goog_api_client": required["goog_api_client"],
    }

    def digest(value: str) -> str:
        return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()

    output_dir = args.output_dir
    atomic_write(output_dir / "session.json", (json.dumps(session, separators=(",", ":")) + "\n").encode())
    atomic_write(output_dir / "account.hash", (digest(str(account_id)) + "\n").encode())
    atomic_write(output_dir / "channel.hash", (digest(str(ai_id)) + "\n").encode())
    atomic_write(output_dir / "speaker.hash", (digest(speaker_id) + "\n").encode())
    print(f"wrote private session material to {output_dir} (mode 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
