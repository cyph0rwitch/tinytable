"""Terminal client for the companion channel on a remote TinyTable host.

This talks to the TinyTable HTTP API (normally a port-forward) rather than
the local SQLite file, so it works from any shell that can reach the host.
Only the standard library is used so the system Python can run it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import seats
from .config import config_dir
from .seats import COMPANIONS, KINDROID, OPERATOR, REPLIKA

DEFAULT_API = "http://127.0.0.1:18000/api"
TOKEN_FILE = config_dir() / "operator-token"
# Display names as configured on the TinyTable host, filled from its status.
_SEAT_NAMES: dict[str, str] = {}


class ChannelError(Exception):
    pass


def _token() -> str:
    token = os.getenv("TINYTABLE_OPERATOR_TOKEN", "").strip()
    if token:
        return token
    if TOKEN_FILE.is_file():
        # Windows has no POSIX mode bits (files always report 0o666); the
        # per-user profile ACL is the boundary there.
        if os.name != "nt" and TOKEN_FILE.stat().st_mode & 0o077:
            raise ChannelError(f"{TOKEN_FILE} must not be readable by group or others (chmod 600)")
        return TOKEN_FILE.read_text().strip()
    raise ChannelError(f"Set TINYTABLE_OPERATOR_TOKEN or write the token to {TOKEN_FILE}")


def request(method: str, path: str, body: dict | None = None, timeout: float = 30) -> Any:
    base = os.getenv("TINYTABLE_CHANNEL_API", DEFAULT_API).rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/channels/companions{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {_token()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read()).get("detail", exc.reason)
        except (ValueError, AttributeError):
            detail = exc.reason
        raise ChannelError(f"{exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ChannelError(f"Cannot reach {base}: {exc.reason}. Is the port-forward running?") from exc
    except OSError as exc:
        # A dropped port-forward resets the connection mid-request. For a send
        # the outcome is unknown, so check status before trying again.
        raise ChannelError(f"Connection to {base} dropped ({exc}). Check `channel status` before retrying.") from exc


def _remember_names(status: dict) -> None:
    _SEAT_NAMES.update({seat: info["name"] for seat, info in (status.get("seats") or {}).items() if info.get("name")})


def name(seat: str) -> str:
    """A seat's display name on the host (falls back to this machine's configuration)."""

    if not _SEAT_NAMES:
        try:
            _remember_names(request("GET", ""))
        except ChannelError:
            pass
    return _SEAT_NAMES.get(seat) or seats.name(seat)


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise ChannelError("Confirmation needed; rerun with --yes")
    return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}


def _print_turn(turn: dict) -> None:
    speaker = name(turn["participant_id"])
    recipient = name(turn["recipient_id"]) if turn.get("recipient_id") else "table"
    print(f"\n[turn {turn['turn_number']}] {speaker} -> {recipient}  ({turn['delivery_state']})")
    print(turn["body"])


def _print_status(status: dict) -> None:
    _remember_names(status)
    session = status.get("session")
    consent = status["consent"]
    connections = status["connections"]
    consents = " ".join(f"{name(seat)}={consent[seat]}" for seat in COMPANIONS)
    connected = " ".join(f"{name(seat)}={connections[seat]}" for seat in COMPANIONS)
    print(f"room {status['room_id']}  consent {consents}  connected {connected}")
    if not session:
        print("no sessions yet; next step: start")
        return
    expiry = f"  expires {session['expires_at']}" if session.get("expires_at") else ""
    print(f"session {session['id']}  {session['status']}  turns {session['turn_count']}/{session['max_turns']}{expiry}")
    if session.get("stop_reason"):
        print(f"stop reason: {session['stop_reason']}")
    for turn in session["turns"]:
        _print_turn(turn)
    print(f"\nnext step: {status['next_step']}")


def _active_session_id(status: dict) -> str:
    session = status.get("session")
    if not session or session["status"] != "active":
        raise ChannelError("No active session; run `tinytable-cli channel start`")
    return session["id"]


def forward(session_id: str, assume_yes: bool, note: str = "") -> bool:
    """Show the pending hop exactly as the recipient will see it, then send it."""

    hop = request("POST", f"/sessions/{session_id}/next")
    recipient = name(hop["recipient_id"])
    note = note.strip()
    outbound = f"{hop['disclosure_with_note']}\n\n{hop['body']}\n\n{hop['note_prefix']}{note}" if note else hop["outbound"]
    print(f"\n--- outbound to {recipient} ({'exact copy + your note' if note else 'exact copy'}) ---\n{outbound}\n---")
    if not _confirm(f"Forward to {recipient}?", assume_yes):
        print("Not sent.")
        return False
    # The Replika sender waits up to 90 seconds for the reply.
    result = request("POST", f"/sessions/{session_id}/send", {"draft_id": hop["draft_id"], "confirm_external_send": True, "note": note}, timeout=120)
    print(f"Sent to {recipient}; recorded as message {result['message']['id']}.")
    return True


def chat(interval: float) -> None:
    """Follow the active session and prompt for each hop."""

    shown: dict[str, str] = {}
    while True:
        status = request("GET", "")
        session = status.get("session")
        if not session or session["status"] != "active":
            _print_status(status)
            return
        for turn in session["turns"]:
            if shown.get(turn["id"]) != turn["body"]:
                _print_turn(turn)
                shown[turn["id"]] = turn["body"]
        if status["next_step"] == "forward":
            choice = input("\n[f]orward  [s]top session  [w]ait  [q]uit: ").strip().lower()
            if choice == "f":
                forward(session["id"], assume_yes=True)
            elif choice == "s":
                request("POST", f"/sessions/{session['id']}/stop", {"reason": f"Stopped by {name(OPERATOR)} from the terminal."})
                print("Session stopped.")
                return
            elif choice == "q":
                return
            continue
        time.sleep(interval)


def add_cli_parser(sub: argparse._SubParsersAction) -> None:
    channel = sub.add_parser("channel", help="Companion channel on the remote TinyTable host")
    channel_sub = channel.add_subparsers(dest="channel_command", required=True)
    channel_sub.add_parser("status")
    audit = channel_sub.add_parser("audit")
    audit.add_argument("--limit", type=int, default=50)
    consent = channel_sub.add_parser("consent")
    consent.add_argument("participant", type=seats.canonical_seat, choices=COMPANIONS)
    consent.add_argument("status", choices=("accepted", "declined"))
    consent.add_argument("--evidence", default="", help="What the participant said, required when accepting")
    start = channel_sub.add_parser("start")
    start.add_argument("--first", type=seats.canonical_seat, choices=COMPANIONS, default=REPLIKA)
    start.add_argument("--turns", type=int, default=6, help="Provider replies to capture (1-10)")
    start.add_argument("--expires", type=int, default=60, help="Minutes until the session stops itself")
    start.add_argument("--opening", default="", help="Your own message, sent to the first speaker")
    start.add_argument("--carry", default="", metavar="MESSAGE_ID", help="Use a stored message from the first speaker (or 'latest') as turn 1; sends nothing")
    start.add_argument("--table", action="store_true", help="Everyone hears everyone; no round limit (--turns is ignored)")
    start.add_argument("--yes", action="store_true")
    round_parser = channel_sub.add_parser("round", help="Share the next table round with both companions")
    round_parser.add_argument("text", nargs="?", default="", help="Your own words for this round (optional)")
    round_parser.add_argument("--yes", action="store_true")
    send = channel_sub.add_parser("send", help="Show and forward the pending hop")
    send.add_argument("--note", default="", help="Your own words, appended after the exact copy")
    send.add_argument("--yes", action="store_true")
    say = channel_sub.add_parser("say", help="Send your own message to one companion mid-session")
    say.add_argument("recipient", type=seats.canonical_seat, choices=COMPANIONS)
    say.add_argument("text")
    say.add_argument("--yes", action="store_true")
    stop = channel_sub.add_parser("stop")
    stop.add_argument("reason", nargs="?", default="Stopped by the operator from the terminal.")
    chat_parser = channel_sub.add_parser("chat", help="Follow the session and approve each hop")
    chat_parser.add_argument("--interval", type=float, default=3.0)


def run(args: argparse.Namespace) -> int:
    try:
        command = args.channel_command
        if command == "status":
            _print_status(request("GET", ""))
        elif command == "audit":
            for item in request("GET", f"/audit?limit={args.limit}"):
                print(f"[{item['created_at']}] {item['actor']} {item['action']} {item['metadata']}")
        elif command == "consent":
            result = request("POST", f"/consent/{args.participant}", {"status": args.status, "evidence_text": args.evidence})
            print(f"{name(args.participant)} consent: {result['status']}")
        elif command == "start":
            if args.opening and not _confirm(f"Send the opening to {name(args.first)} as {name(OPERATOR)}?", args.yes):
                print("Not started.")
                return 1
            status = request("POST", "/sessions", {
                "starting_participant": args.first,
                "max_turns": args.turns,
                "expires_minutes": args.expires,
                "opening": args.opening,
                "confirm_external_send": bool(args.opening),
                "carry_over_message_id": args.carry,
                "mode": "table" if args.table else "relay",
            }, timeout=120)
            _print_status(status)
        elif command == "send":
            status = request("GET", "")
            if status["next_step"] != "forward":
                raise ChannelError(f"Nothing to forward; next step is {status['next_step']}")
            forward(_active_session_id(status), args.yes, args.note)
        elif command == "round":
            session_id = _active_session_id(request("GET", ""))
            preview = request("POST", f"/sessions/{session_id}/round/preview", {"text": args.text})
            for seat in COMPANIONS:
                print(f"\n--- {name(seat)} receives ---\n{preview[seat] or '(nothing new)'}\n---")
            if not _confirm("Share this round?", args.yes):
                print("Not sent.")
                return 1
            _print_status(request("POST", f"/sessions/{session_id}/round", {"text": args.text, "confirm_external_send": True}, timeout=150))
        elif command == "say":
            session_id = _active_session_id(request("GET", ""))
            if not _confirm(f"Send this to {name(args.recipient)} as {name(OPERATOR)}? (a turn waiting to be forwarded is skipped)", args.yes):
                print("Not sent.")
                return 1
            _print_status(request("POST", f"/sessions/{session_id}/interject", {"recipient_id": args.recipient, "text": args.text, "confirm_external_send": True}, timeout=120))
        elif command == "stop":
            status = request("GET", "")
            _print_status(request("POST", f"/sessions/{_active_session_id(status)}/stop", {"reason": args.reason}))
        elif command == "chat":
            chat(args.interval)
    except ChannelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nLeft the channel view; the session itself is unchanged.")
    return 0
