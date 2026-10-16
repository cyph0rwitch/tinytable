"""Local operator controls for TinyTable CLI-agent rooms."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .db import connect, row_dict
from . import seats
from .seats import OPERATOR

ESTATE_ROOM = "cli:hermes-estate"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_agent(participant_id: str) -> dict[str, Any]:
    with connect() as db:
        row = db.execute(
            "SELECT p.*, a.transport, a.target, a.config, a.enabled AS agent_enabled "
            "FROM participants p JOIN cli_agents a ON a.participant_id=p.id "
            "WHERE p.id=? AND p.id IN (SELECT participant_id FROM room_members WHERE room_id=? AND enabled=1)",
            (participant_id, ESTATE_ROOM),
        ).fetchone()
    if not row:
        raise ValueError(f"Unknown or disabled estate agent: {participant_id}")
    item = dict(row)
    item["config"] = json.loads(item["config"] or "{}")
    if not item["agent_enabled"]:
        raise ValueError(f"Estate agent is disabled: {participant_id}")
    return item


def roster() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT p.id, p.display_name, p.description, p.status, p.last_seen_at, "
            "a.transport, a.target, a.enabled "
            "FROM participants p JOIN cli_agents a ON a.participant_id=p.id "
            "JOIN room_members r ON r.participant_id=p.id "
            "WHERE r.room_id=? AND r.enabled=1 ORDER BY p.id",
            (ESTATE_ROOM,),
        ).fetchall()
    return [row_dict(row) for row in rows]


def room_messages(limit: int = 100) -> list[dict[str, Any]]:
    if limit < 1 or limit > 2000:
        raise ValueError("limit must be between 1 and 2000")
    with connect() as db:
        rows = db.execute(
            "SELECT m.*, p.display_name FROM messages m JOIN participants p ON p.id=m.participant_id "
            "WHERE m.room_id=? ORDER BY m.created_at DESC LIMIT ?",
            (ESTATE_ROOM, limit),
        ).fetchall()
    return list(reversed([row_dict(row) for row in rows]))


def post(body: str) -> dict[str, Any]:
    if not body.strip():
        raise ValueError("Message cannot be empty")
    return _save_cli_message(
        OPERATOR,
        body,
        "human",
        "tinytable-cli",
        raw_metadata={"transport": "tinytable_cli", "origin": "explicit_operator_action"},
    )


def agent_ids() -> list[str]:
    """IDs of the enabled agents in the CLI room."""

    return [item["id"] for item in roster() if item["enabled"]]


def relay(author: str, body: str) -> dict[str, Any]:
    if author not in agent_ids():
        raise ValueError(f"Relay author must be one of: {', '.join(agent_ids()) or 'no agents configured'}")
    if not body.strip():
        raise ValueError("Message cannot be empty")
    return _save_cli_message(
        author,
        body,
        "manual",
        "codex-relay",
        raw_metadata={
            "transport": "codex_relay",
            "origin": "received_from_codex_relay",
            "original_author": author,
        },
    )


def _save_cli_message(
    participant_id: str,
    body: str,
    source_type: str,
    provider: str,
    *,
    request_id: str | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a CLI-room event without requiring the FastAPI dependency."""

    body = body.strip()
    if not body:
        raise ValueError("Message cannot be empty")
    message = {
        "id": str(uuid.uuid4()),
        "room_id": ESTATE_ROOM,
        "participant_id": participant_id,
        "body": body,
        "created_at": now(),
        "provider": provider,
        "model": None,
        "request_id": request_id,
        "source_type": source_type,
        "raw_metadata": raw_metadata or {},
        "mentions": [],
        "statement_source": "human_statement" if participant_id == OPERATOR else "model_inference",
        "identity_boundary_status": "not_checked",
        "identity_boundary_warning": None,
        "assistance_author": None,
        "approval_status": "not_applicable",
        "disclosure_status": "not_applicable",
        "source_draft_id": None,
    }
    with connect() as db:
        if not db.execute("SELECT 1 FROM rooms WHERE id=?", (ESTATE_ROOM,)).fetchone():
            raise ValueError(f"Room not found: {ESTATE_ROOM}")
        if not db.execute("SELECT 1 FROM participants WHERE id=?", (participant_id,)).fetchone():
            raise ValueError(f"Participant not found: {participant_id}")
        db.execute(
            """INSERT INTO messages
            (id, room_id, participant_id, body, created_at, provider, model, request_id,
             source_type, raw_metadata, mentions, statement_source, identity_boundary_status,
             identity_boundary_warning, assistance_author, approval_status,
             disclosure_status, source_draft_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message["id"], message["room_id"], message["participant_id"], message["body"],
                message["created_at"], message["provider"], message["model"], message["request_id"],
                message["source_type"], json.dumps(message["raw_metadata"]), json.dumps(message["mentions"]),
                message["statement_source"], message["identity_boundary_status"],
                message["identity_boundary_warning"], message["assistance_author"],
                message["approval_status"], message["disclosure_status"], message["source_draft_id"],
            ),
        )
    return message


def _command(agent: dict[str, Any], body: str) -> list[str]:
    hermes = os.getenv("TINYTABLE_HERMES_BIN", "hermes")
    if os.name == "nt":
        hermes = shutil.which(hermes) or hermes
        if hermes.lower().endswith((".cmd", ".bat")):
            # cmd.exe would reinterpret message text; require a real executable.
            raise ValueError(f"Refusing batch-file Hermes shim {hermes}; set TINYTABLE_HERMES_BIN to an .exe")
    if agent["transport"] == "hermes_local":
        profile = agent["config"].get("profile", "default")
        command = [hermes]
        if profile != "default":
            command.extend(["-p", profile])
        command.extend(["chat", "-q", body, "--oneshot"])
        return command
    if agent["transport"] == "hermes_peer":
        return [hermes, "peer", "dm", agent["target"], body]
    raise ValueError(f"Unsupported CLI transport: {agent['transport']}")


def _set_presence(participant_id: str, source: str) -> None:
    with connect() as db:
        db.execute(
            "UPDATE participants SET last_seen_at=?, last_seen_source=? WHERE id=?",
            (now(), source, participant_id),
        )


def _resolve_message(value: str) -> tuple[str, bool]:
    """Return an estate operator message ID; post ordinary text once if needed."""

    with connect() as db:
        row = db.execute(
            "SELECT id FROM messages WHERE id=? AND room_id=? AND participant_id='operator'",
            (value, ESTATE_ROOM),
        ).fetchone()
    if row:
        return row["id"], False
    try:
        uuid.UUID(value)
    except ValueError:
        return post(value)["id"], True
    raise ValueError("Message ID was not found in the estate room")


def send(message_id: str, target: str, timeout: float | None = None) -> dict[str, Any]:
    agent = _load_agent(target)
    timeout = timeout or float(os.getenv("TINYTABLE_CLI_AGENT_TIMEOUT", "180"))
    message_id, posted = _resolve_message(message_id)
    with connect() as db:
        source = db.execute(
            "SELECT * FROM messages WHERE id=? AND room_id=? AND participant_id='operator'",
            (message_id, ESTATE_ROOM),
        ).fetchone()
    if not source:
        raise ValueError(f"Message must be a {seats.name(OPERATOR)} message in the estate room")

    attempt_id = str(uuid.uuid4())
    created = now()
    with connect() as db:
        db.execute(
            "INSERT INTO delivery_attempts (id, room_id, message_id, target_participant_id, transport, state, created_at, metadata) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
            (attempt_id, ESTATE_ROOM, message_id, target, agent["transport"], created, json.dumps({"target": agent["target"]})),
        )
    command = _command(agent, source["body"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        state = "timeout"
        error = f"Timed out after {timeout:g}s"
        output = ""
        external_id = None
        _finish_attempt(attempt_id, state, error, external_id)
        return {"attempt_id": attempt_id, "message_id": message_id, "posted": posted, "state": state, "error": error, "output": output}
    if result.returncode != 0:
        state = "failed"
        error = f"Hermes exited with status {result.returncode}"
        _finish_attempt(attempt_id, state, error, None)
        return {"attempt_id": attempt_id, "message_id": message_id, "posted": posted, "state": state, "error": error, "output": result.stdout.strip()}

    output = result.stdout.strip()
    if not output:
        state = "failed"
        error = "Hermes returned no response text"
        _finish_attempt(attempt_id, state, error, None)
        return {"attempt_id": attempt_id, "message_id": message_id, "posted": posted, "state": state, "error": error, "output": output}

    response = _save_cli_message(
        target,
        output,
        "remote_model",
        "hermes",
        request_id=f"hermes:{attempt_id}",
        raw_metadata={
            "transport": agent["transport"],
            "target": agent["target"],
            "origin": "received_from_provider",
            "delivery_attempt_id": attempt_id,
            "command": command[:3] if agent["transport"] == "hermes_local" else command[:4],
        },
    )
    _set_presence(target, agent["transport"])
    _finish_attempt(attempt_id, "delivered", None, response["id"])
    return {"attempt_id": attempt_id, "message_id": message_id, "posted": posted, "state": "delivered", "message": response}


def send_all(message_id: str, timeout: float | None = None) -> dict[str, dict[str, Any]]:
    """Explicitly dispatch one operator message to every enabled estate agent."""

    resolved_id, posted = _resolve_message(message_id)
    results = {}
    for target in agent_ids():
        result = send(resolved_id, target, timeout)
        result["posted"] = posted
        results[target] = result
    return results


def _finish_attempt(attempt_id: str, state: str, error: str | None, external_id: str | None) -> None:
    with connect() as db:
        db.execute(
            "UPDATE delivery_attempts SET state=?, error=?, external_id=?, completed_at=? WHERE id=?",
            (state, error, external_id, now(), attempt_id),
        )


def _print_message(message: dict[str, Any]) -> None:
    print(f"[{message['created_at']}] {message.get('display_name', message['participant_id'])}: {message['body']}")


def cli(argv: list[str] | None = None) -> int:
    from .db import init_db

    init_db()
    parser = argparse.ArgumentParser(prog="tinytable-cli")
    sub = parser.add_subparsers(dest="command", required=True)
    from .channel_cli import add_cli_parser as add_channel_parser
    add_channel_parser(sub)
    room = sub.add_parser("room")
    room_sub = room.add_subparsers(dest="room_command", required=True)
    room_sub.add_parser("roster")
    list_parser = room_sub.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=100)
    post_parser = room_sub.add_parser("post")
    post_parser.add_argument("body")
    relay_parser = room_sub.add_parser("relay")
    relay_parser.add_argument("--from", dest="author", required=True, help="agent ID (see `room roster`)")
    relay_parser.add_argument("body")
    send_parser = room_sub.add_parser("send")
    send_parser.add_argument("--to", required=True, help="agent ID (see `room roster`), or all")
    send_parser.add_argument("--message", required=True)
    send_parser.add_argument("--timeout", type=float, default=None)
    watch_parser = room_sub.add_parser("watch")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--limit", type=int, default=100)
    from .commons_bridge import add_cli_parser
    add_cli_parser(sub)
    args = parser.parse_args(argv)
    try:
        if args.command == "channel":
            from .channel_cli import run as run_channel
            return run_channel(args)
        if args.command == "commons":
            from .commons_bridge import export_command
            return export_command(args)
        if args.room_command == "roster":
            print(json.dumps(roster(), indent=2))
        elif args.room_command == "list":
            for item in room_messages(args.limit):
                _print_message(item)
        elif args.room_command == "post":
            print(json.dumps(post(args.body), indent=2))
        elif args.room_command == "relay":
            print(json.dumps(relay(args.author, args.body), indent=2))
        elif args.room_command == "send":
            result = send_all(args.message, args.timeout) if args.to == "all" else send(args.message, args.to, args.timeout)
            print(json.dumps(result, indent=2))
        elif args.room_command == "watch":
            seen: set[str] = set()
            while True:
                for item in room_messages(args.limit):
                    if item["id"] not in seen:
                        _print_message(item)
                        seen.add(item["id"])
                if args.once:
                    break
                time.sleep(1)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
