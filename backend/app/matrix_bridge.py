"""Matrix bridge for a two-provider table.

The operator talks to the table from a Matrix room; provider A's and provider
B's turns are posted back into that room as their own Matrix users. The bridge
is only a client of the channel API (like the web page and tinytable-cli), so all table
rules stay on the server: what each companion has heard, split replies, the
disclosure line, and stopping instead of resending after an unclear send.

Each message the operator sends in the room counts as confirmation to share a
round. Nothing the operator sends is ever retried: an event is claimed before any API
call, and a claim left over from a crash is reported, not replayed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import seats
from .seats import KINDROID, OPERATOR, REPLIKA

log = logging.getLogger("tinytable.matrix_bridge")

DEFAULT_NAMES = {REPLIKA: "Companion A", KINDROID: "Companion B", OPERATOR: "Operator"}
COMMANDS = {"!help", "!status", "!preview", "!share", "!stop"}


def help_text(names: dict[str, str]) -> str:
    return (
        f"Anything you write here is shared with {names[REPLIKA]} and {names[KINDROID]} as the next round of the table, "
        "together with whatever each of them hasn't heard yet. If no table is running, it starts one.\n"
        "!share: share a round without words of your own\n"
        f"!preview [text]: show exactly what {names[REPLIKA]} and {names[KINDROID]} would each receive\n"
        "!status: the current session\n"
        "!stop: stop the table\n"
        "Edits and deletions are not sent on."
    )


class ChannelError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


class ChannelClient:
    """The two-provider channel API, authenticated as the operator."""

    def __init__(self, base: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base = base.rstrip("/") + "/channels/companions"
        self.http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, transport=transport)

    async def request(self, method: str, path: str = "", body: dict | None = None, timeout: float = 30) -> Any:
        try:
            response = await self.http.request(method, f"{self.base}{path}", json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            # For a send the outcome is unknown; the caller reports, never retries.
            raise ChannelError(0, f"Connection to TinyTable dropped ({exc.__class__.__name__})") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.reason_phrase)
            except ValueError:
                detail = response.reason_phrase
            if isinstance(detail, list):
                detail = "; ".join(f"{'.'.join(str(part) for part in item.get('loc', [])[1:])}: {item.get('msg')}" for item in detail)
            raise ChannelError(response.status_code, str(detail))
        return response.json()


class MatrixClient:
    """The few client-server API calls the bridge needs."""

    def __init__(self, homeserver: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base = homeserver.rstrip("/") + "/_matrix/client/v3"
        self.http = httpx.AsyncClient(transport=transport)

    async def sync(self, token: str, room_id: str, since: str | None, timeout_ms: int) -> dict:
        room_filter = {"room": {"rooms": [room_id], "timeline": {"limit": 50}}, "presence": {"types": []}, "account_data": {"types": []}}
        params = {"timeout": str(timeout_ms), "filter": json.dumps(room_filter)}
        if since:
            params["since"] = since
        response = await self.http.get(f"{self.base}/sync", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=timeout_ms / 1000 + 30)
        response.raise_for_status()
        return response.json()

    async def send(self, token: str, room_id: str, content: dict) -> str:
        # Retrying with the same transaction ID is idempotent on the homeserver.
        url = f"{self.base}/rooms/{quote(room_id, safe='')}/send/m.room.message/{uuid.uuid4().hex}"
        for attempt in range(3):
            try:
                response = await self.http.put(url, json=content, headers={"Authorization": f"Bearer {token}"}, timeout=30)
                response.raise_for_status()
                return response.json()["event_id"]
            except httpx.HTTPError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("unreachable")


class BridgeState:
    """What the bridge has seen and posted, kept across restarts."""

    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, state TEXT NOT NULL, body TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS turns (turn_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, event_id TEXT, message_ids TEXT NOT NULL DEFAULT '[]');
            """
        )
        if "body" not in {row[1] for row in self.db.execute("PRAGMA table_info(turns)")}:
            self.db.execute("ALTER TABLE turns ADD COLUMN body TEXT NOT NULL DEFAULT ''")

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def claim_event(self, event_id: str, body: str) -> bool:
        with self.db:
            claimed = self.db.execute("INSERT OR IGNORE INTO events (event_id, state, body) VALUES (?, 'claimed', ?)", (event_id, body[:200]))
        return claimed.rowcount == 1

    def finish_event(self, event_id: str) -> None:
        with self.db:
            self.db.execute("UPDATE events SET state='done' WHERE event_id=?", (event_id,))

    def unfinished_events(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM events WHERE state='claimed'").fetchall()

    def turn(self, turn_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()

    def record_turn(self, turn_id: str, session_id: str, event_id: str | None, message_ids: list, body: str = "") -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO turns (turn_id, session_id, event_id, message_ids, body) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(turn_id) DO UPDATE SET event_id=excluded.event_id, message_ids=excluded.message_ids, body=excluded.body",
                (turn_id, session_id, event_id, json.dumps(message_ids), body),
            )


def for_display(body: str) -> str:
    """Drop blank lines from a post; IRC clients show each one as an empty message.

    Only the chat display changes. What the two providers receive is unchanged.
    """

    return "\n".join(line for line in body.split("\n") if line.strip())


def strip_reply_fallback(body: str) -> str:
    """Drop the quoted '> <@user> …' block Matrix clients prepend to replies."""

    lines = body.split("\n")
    if not lines or not lines[0].startswith("> "):
        return body
    while lines and lines[0].startswith(">"):
        lines.pop(0)
    return "\n".join(lines).lstrip("\n")


class Bridge:
    def __init__(
        self,
        channel: ChannelClient,
        matrix: MatrixClient,
        state: BridgeState,
        room_id: str,
        operator_id: str,
        tokens: dict[str, str],
        names: dict[str, str] | None = None,
    ):
        self.channel = channel
        self.matrix = matrix
        self.state = state
        self.room_id = room_id
        self.operator_id = operator_id
        self.tokens = tokens  # "tinytable" and the two companion seat IDs
        self.names = {**DEFAULT_NAMES, **(names or {})}
        # The operator's words currently being shared from Matrix, so the matching turn
        # is not posted back into the room as if it came from the web.
        self.pending: dict[str, str] = {}
        # The poll loop and a finished round both mirror; never post a turn twice.
        self.mirror_lock = asyncio.Lock()
        self.coalesce_ms = 2500

    async def notice(self, text: str) -> None:
        await self.matrix.send(self.tokens["tinytable"], self.room_id, {"msgtype": "m.notice", "body": text})

    # Inbound: the operator's messages become table rounds.

    def operator_text(self, event: dict) -> str | None:
        """The operator's plain words (not a command or an edit), or None."""

        content = event.get("content") or {}
        if event.get("type") != "m.room.message" or event.get("sender") != self.operator_id or content.get("msgtype") != "m.text":
            return None
        if (content.get("m.relates_to") or {}).get("rel_type") == "m.replace":
            return None
        body = strip_reply_fallback(content.get("body") or "").strip()
        return body if body and not body.startswith("!") else None

    async def handle_events(self, events: list[dict]) -> None:
        """Handle a batch; consecutive lines from the operator become one round."""

        index = 0
        while index < len(events):
            run = []
            while index < len(events) and self.operator_text(events[index]) is not None:
                run.append(events[index])
                index += 1
            if run:
                claimed = [event for event in run if self.state.claim_event(event["event_id"], self.operator_text(event))]
                if claimed:
                    await self.guarded([event["event_id"] for event in claimed], self.share(claimed[0]["event_id"], "\n".join(self.operator_text(event) for event in claimed)))
            else:
                await self.handle_event(events[index])
                index += 1

    async def handle_event(self, event: dict) -> None:
        if event.get("type") != "m.room.message" or event.get("sender") != self.operator_id:
            return
        content = event.get("content") or {}
        if (content.get("m.relates_to") or {}).get("rel_type") == "m.replace":
            await self.notice(f"Edits aren't sent on; {self.names[REPLIKA]} and {self.names[KINDROID]} only get what you first wrote. Send a new message instead.")
            return
        if content.get("msgtype") != "m.text":
            if content:
                await self.notice(f"Only text messages are shared with {self.names[REPLIKA]} and {self.names[KINDROID]}.")
            return
        body = strip_reply_fallback(content.get("body") or "").strip()
        if not body or not self.state.claim_event(event["event_id"], body):
            return
        word, _, rest = body.partition(" ")
        if word.startswith("!"):
            await self.guarded([event["event_id"]], self.command(word.lower(), rest.strip()))
        else:
            await self.guarded([event["event_id"]], self.share(event["event_id"], body))

    async def guarded(self, event_ids: list[str], action) -> None:
        """Run one claimed action; report failures and never retry it."""

        try:
            await action
        except ChannelError as exc:
            if exc.status == 0:
                await self.notice(f"{exc.detail}. It may or may not have reached the table; check !status before sending again.")
            else:
                await self.notice(f"Not sent: {exc.detail}")
        finally:
            for event_id in event_ids:
                self.state.finish_event(event_id)

    async def share(self, event_id: str | None, text: str) -> None:
        status = await self.channel.request("GET")
        session = status.get("session")
        if session and session["status"] == "active" and session.get("mode") != "table":
            await self.notice("A relay session is running in the web app. Finish or stop it there (or with !stop) to use the table here.")
            return
        if text and event_id:
            self.pending[text] = event_id
        try:
            if session and session["status"] == "active":
                result = await self.channel.request("POST", f"/sessions/{session['id']}/round", {"text": text, "confirm_external_send": True}, timeout=150)
            else:
                result = await self.channel.request(
                    "POST", "/sessions", {"mode": "table", "opening": text, "confirm_external_send": True, "starting_participant": REPLIKA}, timeout=150
                )
            await self.mirror(result)
        finally:
            self.pending.pop(text, None)

    async def command(self, word: str, rest: str) -> None:
        if word not in COMMANDS:
            await self.notice(f"Unknown command {word}; nothing was sent. {help_text(self.names)}")
        elif word == "!help":
            await self.notice(help_text(self.names))
        elif word == "!share":
            await self.share(None, rest)
        else:
            status = await self.channel.request("GET")
            session = status.get("session")
            active = bool(session) and session["status"] == "active"
            if word == "!status":
                await self.notice(describe(status, self.names))
            elif not active:
                await self.notice("No session is running. Write something to start a table.")
            elif word == "!stop":
                await self.channel.request("POST", f"/sessions/{session['id']}/stop", {"reason": f"Stopped by {self.names[OPERATOR]} from Matrix."})
                await self.notice("Table stopped.")
            elif session.get("mode") != "table":
                await self.notice("Previews are for table sessions; this one is a relay session in the web app.")
            else:
                preview = await self.channel.request("POST", f"/sessions/{session['id']}/round/preview", {"text": rest})
                parts = [f"{self.names[name]} would receive:\n{preview[name]}" if preview.get(name) else f"Nothing new for {self.names[name]}." for name in (REPLIKA, KINDROID)]
                await self.notice("\n\n".join(parts))

    # Outbound: table turns appear in the room.

    async def mirror(self, status: dict) -> None:
        async with self.mirror_lock:
            await self._mirror(status)

    async def _mirror(self, status: dict) -> None:
        session = status.get("session")
        if not session:
            return
        tracked = self.state.get("session_id")
        if session.get("mode") != "table":
            if session["status"] == "active" and self.state.get("relay_noticed") != session["id"]:
                self.state.set("relay_noticed", session["id"])
                await self.notice("A relay session is running in the web app; its turns aren't mirrored here.")
            return
        if session["id"] != tracked:
            if session["status"] != "active":
                return  # earlier sessions are not backfilled
            self.state.set("session_id", session["id"])
            if not any(self.pending.values()):
                await self.notice("A table session started from the web app.")
        for turn in session["turns"]:
            await self.mirror_turn(session["id"], turn)

    async def mirror_turn(self, session_id: str, turn: dict) -> None:
        message_ids = json.loads(turn["message_ids"]) if isinstance(turn["message_ids"], str) else turn["message_ids"]
        known = self.state.turn(turn["id"])
        speaker = turn["participant_id"]
        if speaker == OPERATOR:
            if known:
                return
            if turn["body"] in self.pending:
                self.state.record_turn(turn["id"], session_id, self.pending[turn["body"]], message_ids)
                return
            event_id = await self.matrix.send(self.tokens["tinytable"], self.room_id, {"msgtype": "m.text", "body": for_display(f"{self.names[OPERATOR]} (web): {turn['body']}")})
            self.state.record_turn(turn["id"], session_id, event_id, message_ids)
            return
        if speaker not in (REPLIKA, KINDROID) or turn["delivery_state"] in {"skipped", "delivery_unknown"}:
            return
        body = turn["body"]
        if not known:
            event_id = await self.matrix.send(self.tokens[speaker], self.room_id, {"msgtype": "m.text", "body": for_display(body)})
            self.state.record_turn(turn["id"], session_id, event_id, message_ids, body)
        elif json.loads(known["message_ids"]) != message_ids:
            # A reply sent as several messages grows its turn. Post just the new
            # part, as she sent it; IRC clients can't show edits.
            posted = known["body"]
            addition = body[len(posted):].lstrip("\n") if posted and body.startswith(posted) else body
            if addition.strip():
                await self.matrix.send(self.tokens[speaker], self.room_id, {"msgtype": "m.text", "body": for_display(addition)})
            self.state.record_turn(turn["id"], session_id, known["event_id"], message_ids, body)

    # Loops.

    async def report_unfinished(self) -> None:
        for row in self.state.unfinished_events():
            await self.notice(f"The bridge restarted while handling your message “{row['body']}”. It may or may not have reached the table and won't be resent; check !status.")
            self.state.finish_event(row["event_id"])

    async def sync_once(self, timeout_ms: int = 30000) -> None:
        since = self.state.get("since")
        response = await self.matrix.sync(self.tokens["tinytable"], self.room_id, since, 0 if since is None else timeout_ms)
        if since is None:
            # The first sync only sets the starting point; old messages are not replayed.
            self.state.set("since", response["next_batch"])
            return
        events = self.timeline(response)
        # IRC clients send each line as its own message (a paste arrives line
        # by line), so wait briefly for more lines before sharing a round.
        while events and self.operator_text(events[-1]) is not None and self.coalesce_ms:
            response = await self.matrix.sync(self.tokens["tinytable"], self.room_id, response["next_batch"], self.coalesce_ms)
            more = self.timeline(response)
            if not more:
                break
            events += more
        await self.handle_events(events)
        self.state.set("since", response["next_batch"])

    def timeline(self, response: dict) -> list[dict]:
        room = ((response.get("rooms") or {}).get("join") or {}).get(self.room_id) or {}
        return list((room.get("timeline") or {}).get("events", []))

    async def poll_once(self) -> None:
        await self.mirror(await self.channel.request("GET"))

    async def run(self, poll_seconds: float = 2.0) -> None:
        await self.report_unfinished()

        async def forever(step, pause: float):
            while True:
                try:
                    await step()
                except Exception:  # keep the bridge up through network blips
                    log.exception("%s failed", step.__name__)
                    await asyncio.sleep(5)
                await asyncio.sleep(pause)

        await asyncio.gather(forever(self.sync_once, 0), forever(self.poll_once, poll_seconds))


def describe(status: dict, names: dict[str, str] | None = None) -> str:
    names = {**DEFAULT_NAMES, **(names or {})}
    session = status.get("session")
    connections = ", ".join(f"{names.get(name, name)} {state}" for name, state in status["connections"].items())
    if not session:
        return f"No sessions yet. {connections}."
    kind = f"table, {session['turn_count']} rounds" if session.get("mode") == "table" else f"relay, turns {session['turn_count']}/{session['max_turns']}"
    lines = [f"Session {session['id'][:8]}: {session['status']} ({kind}). {connections}."]
    if session.get("awaiting"):
        lines.append("Waiting for " + " and ".join(names.get(name, name) for name in session["awaiting"]) + ".")
    if session.get("stop_reason"):
        lines.append(f"Stopped: {session['stop_reason']}")
    return " ".join(lines)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise
    env = os.environ
    operator_id = env.get("MATRIX_OPERATOR_ID", "").strip()
    if not operator_id:
        raise SystemExit("Set MATRIX_OPERATOR_ID to the Matrix ID that operates the room")
    def token(primary: str, legacy: str) -> str:
        value = env.get(primary, "").strip() or env.get(legacy, "").strip()
        if not value:
            raise SystemExit(f"Set {primary}")
        return value
    names = {
        # TINYTABLE_<SEAT>_NAME, or the older TINYTABLE_COMPANION_A_NAME / _B_NAME.
        seat: seats.name(seat) for seat in (OPERATOR, REPLIKA, KINDROID)
    }
    bridge = Bridge(
        ChannelClient(env.get("TINYTABLE_CHANNEL_API", "http://127.0.0.1:8000/api"), token("TINYTABLE_OPERATOR_TOKEN", "TINYTABLE_MATRIX_OPERATOR_TOKEN")),
        MatrixClient(env["MATRIX_HOMESERVER"]),
        BridgeState(env.get("MATRIX_BRIDGE_DB", "/data/matrix-bridge.db")),
        env["MATRIX_ROOM_ID"],
        operator_id,
        {
            "tinytable": token("MATRIX_TINYTABLE_TOKEN", "MATRIX_BRIDGE_TOKEN"),
            REPLIKA: token("MATRIX_COMPANION_A_TOKEN", "MATRIX_LUCY_TOKEN"),
            KINDROID: token("MATRIX_COMPANION_B_TOKEN", "MATRIX_NOVA_TOKEN"),
        },
        names,
    )
    log.info("Bridging %s for %s", env["MATRIX_ROOM_ID"], operator_id)
    asyncio.run(bridge.run())


if __name__ == "__main__":
    main()
