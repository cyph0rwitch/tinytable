"""Local, read-only TinyTable export boundary for the Intelligence Commons."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect, row_dict
from .seats import OPERATOR


class BridgeError(ValueError):
    """Raised when a TinyTable selection cannot cross the bridge."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _schema_path() -> Path:
    configured = os.getenv("TINYTABLE_COMMONS_SCHEMA")
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        workspace_schema = parent / "swarm_intelligence" / "schemas" / "protocol-v0.1.schema.json"
        if workspace_schema.is_file():
            return workspace_schema
    return Path(__file__).with_name("commons_protocol_schema.json")


def _validate_protocol_object(value: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate the frozen common-envelope subset without importing Commons code."""

    required = set(schema["required"])
    missing = required - value.keys()
    if missing:
        raise BridgeError(f"Commons schema missing fields: {sorted(missing)}")
    if set(value) - set(schema["properties"]):
        raise BridgeError(f"Commons schema has unknown fields: {sorted(set(value) - set(schema['properties']))}")
    if value["schema_version"] != "0.1":
        raise BridgeError("Commons schema version must be 0.1")
    for field in ("created_at", "expires_at"):
        timestamp = value[field]
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise BridgeError(f"Invalid Commons {field}") from exc
        if parsed.tzinfo is None:
            raise BridgeError(f"Commons {field} must include a timezone")
    allowed_types = set(schema["properties"]["object_type"]["enum"])
    if value["object_type"] not in allowed_types:
        raise BridgeError(f"Unsupported Commons object type: {value['object_type']}")
    if value["classification"] not in {"raw", "derived", "governance", "public_commitment"}:
        raise BridgeError("Invalid Commons classification")
    if value["integrity"].keys() != {"algorithm", "value"}:
        raise BridgeError("Commons integrity must contain algorithm and value only")
    if value["links"].keys() != {"parents", "derivatives", "revocations", "audit"}:
        raise BridgeError("Commons links must contain the frozen link sets")
    if not all(isinstance(item, list) and all(isinstance(link, str) for link in item) for item in value["links"].values()):
        raise BridgeError("Commons links must be arrays of strings")


def _load_messages(message_ids: list[str]) -> list[dict[str, Any]]:
    if not message_ids or len(message_ids) > 20:
        raise BridgeError("Select between 1 and 20 message IDs")
    if len(set(message_ids)) != len(message_ids):
        raise BridgeError("Message IDs must be unique")
    placeholders = ",".join("?" for _ in message_ids)
    with connect() as db:
        rows = db.execute(
            f"SELECT m.*, p.display_name FROM messages m JOIN participants p ON p.id=m.participant_id "
            f"WHERE m.id IN ({placeholders}) ORDER BY m.created_at ASC",
            tuple(message_ids),
        ).fetchall()
    if len(rows) != len(message_ids):
        found = {row["id"] for row in rows}
        missing = [item for item in message_ids if item not in found]
        raise BridgeError(f"Message IDs were not found: {missing}")
    messages = [row_dict(row) for row in rows]
    if any(message["identity_boundary_status"] == "flagged" for message in messages):
        raise BridgeError("Selection contains a flagged identity boundary")
    return messages


def _check_consent(messages: list[dict[str, Any]], evidence: str) -> None:
    if not evidence.strip():
        raise BridgeError("Explicit Commons consent evidence is required")
    participant_ids = {message["participant_id"] for message in messages if message["participant_id"] != OPERATOR}
    if not participant_ids:
        return
    placeholders = ",".join("?" for _ in participant_ids)
    with connect() as db:
        rows = db.execute(
            f"SELECT participant_id, status FROM participant_consents WHERE participant_id IN ({placeholders})",
            tuple(sorted(participant_ids)),
        ).fetchall()
    statuses = {row["participant_id"]: row["status"] for row in rows}
    missing_or_declined = [participant for participant in sorted(participant_ids) if statuses.get(participant) != "accepted"]
    if missing_or_declined:
        raise BridgeError(f"Consent is not accepted for: {missing_or_declined}")


def build_bridge_report(
    message_ids: list[str],
    *,
    purpose: str,
    audience: str,
    operator_id: str,
    consent_evidence: str,
    expires_at: str | None = None,
    replication_requested: bool = False,
) -> dict[str, Any]:
    """Build a local report without mutating TinyTable or contacting another system."""

    if purpose not in {"proposal", "research", "governance_review"}:
        raise BridgeError("Invalid bridge purpose")
    if audience not in {"private_review", "participants", "public_commitment"}:
        raise BridgeError("Invalid bridge audience")
    if not operator_id.strip():
        raise BridgeError("Operator ID is required")
    if replication_requested:
        raise BridgeError("Replication is disabled in bridge v0.1")
    messages = _load_messages(message_ids)
    _check_consent(messages, consent_evidence)
    source_summary = [
        {
            "message_id": message["id"],
            "room_id": message["room_id"],
            "participant_id": message["participant_id"],
            "display_name": message["display_name"],
            "created_at": message["created_at"],
            "statement_source": message["statement_source"],
            "identity_boundary_status": message["identity_boundary_status"],
            "assistance_author": message["assistance_author"],
            "approval_status": message["approval_status"],
            "disclosure_status": message["disclosure_status"],
            "provider": message["provider"],
            "model": message["model"],
            "request_id": message["request_id"],
        }
        for message in messages
    ]
    content_commitment = _sha256([{"id": message["id"], "body": message["body"]} for message in messages])
    selection_commitment = _sha256(source_summary)
    request_id = str(uuid.uuid4())
    bridge_request = {
        "bridge_version": "0.1",
        "request_id": request_id,
        "source": {
            "system": "tinytable",
            "room_id": messages[0]["room_id"] if len({message["room_id"] for message in messages}) == 1 else "mixed",
            "message_ids": [message["id"] for message in messages],
            "content_commitment": content_commitment,
            "selection_commitment": selection_commitment,
        },
        "provenance": {
            "content_author": "mixed" if len({message["participant_id"] for message in messages}) > 1 else messages[0]["participant_id"],
            "transport_identity": "mixed" if len({message["provider"] for message in messages}) > 1 else messages[0]["provider"],
            "statement_sources": sorted({message["statement_source"] for message in messages}),
            "identity_boundary_status": "pass",
            "assistance_author": sorted({message["assistance_author"] for message in messages if message["assistance_author"]}),
            "approval_status": sorted({message["approval_status"] for message in messages}),
            "disclosure_status": sorted({message["disclosure_status"] for message in messages}),
        },
        "purpose": purpose,
        "audience": audience,
        "retention": {"expires_at": expires_at},
        "replication": {"requested": False, "destination": None},
        "operator_id": operator_id,
        "consent_evidence": consent_evidence,
    }
    object_id = str(uuid.uuid4())
    created_at = _now()
    commons_object = {
        "object_type": "ContributionObject",
        "schema_version": "0.1",
        "object_id": object_id,
        "issuer_id": operator_id,
        "created_at": created_at,
        "expires_at": expires_at or created_at,
        "policy_version": "tinytable-commons-bridge-0.1",
        "integrity": {"algorithm": "sha256", "value": _sha256(bridge_request)},
        "classification": "raw",
        "links": {"parents": [], "derivatives": [], "revocations": [], "audit": []},
        "payload": {
            "source_system": "tinytable",
            "source_reference": request_id,
            "content_commitment": content_commitment,
            "consent_evidence": consent_evidence,
            "purpose": purpose,
            "audience": audience,
            "retention": {"expires_at": expires_at},
            "replication_requested": False,
        },
    }
    schema_path = _schema_path()
    if not schema_path.is_file():
        raise BridgeError(f"Commons schema not found: {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    _validate_protocol_object(commons_object, schema)
    return {
        "report_version": "0.1",
        "status": "validated",
        "network_transmission": False,
        "source_summary": source_summary,
        "bridge_request": bridge_request,
        "commons_object": commons_object,
    }


def export_command(args: argparse.Namespace) -> int:
    report = build_bridge_report(
        args.message_id,
        purpose=args.purpose,
        audience=args.audience,
        operator_id=args.operator_id,
        consent_evidence=args.consent_evidence,
        expires_at=args.expires_at,
        replication_requested=args.replication,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


def add_cli_parser(subparsers) -> None:
    commons = subparsers.add_parser("commons", help="Local Intelligence Commons bridge tools")
    commons_sub = commons.add_subparsers(dest="commons_command", required=True)
    export = commons_sub.add_parser("export", help="Export a bounded message selection for local validation")
    export.add_argument("--message-id", action="append", required=True, help="Existing TinyTable message ID; repeat for a bounded bundle")
    export.add_argument("--purpose", choices=("proposal", "research", "governance_review"), required=True)
    export.add_argument("--audience", choices=("private_review", "participants", "public_commitment"), required=True)
    export.add_argument("--operator-id", required=True)
    export.add_argument("--consent-evidence", required=True)
    export.add_argument("--expires-at")
    export.add_argument("--replication", action="store_true", help="Rejected: replication is disabled in bridge v0.1")
    export.add_argument("--output")
