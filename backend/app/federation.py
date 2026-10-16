"""Signed room-event federation primitives for the TinyTable prototype.

This module deliberately contains no discovery or model-routing logic.  A host
uses it as an addressable, signed event log and a small WebSocket fan-out
helper.
"""

from __future__ import annotations

import base64
import hashlib
import heapq
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature


PROTOCOL_VERSION = "tinytable-federation-v0.1"
MAX_REFERENCES = 32


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def generate_test_keypair(seed: bytes | None = None) -> dict[str, str]:
    """Return a test keypair; callers must not use this as production key storage."""

    if seed is None:
        private = Ed25519PrivateKey.generate()
    else:
        private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest())
    public = private.public_key()
    return {"private_key": _b64(private.private_bytes_raw()), "public_key": _b64(public.public_bytes_raw())}


def event_without_signature(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "signature"}


def sign_event(event: dict[str, Any], private_key: str) -> dict[str, Any]:
    unsigned = event_without_signature(event)
    signed = dict(unsigned)
    signed["signature"] = _b64(Ed25519PrivateKey.from_private_bytes(_unb64(private_key)).sign(canonical(unsigned)))
    return signed


def verify_event(event: dict[str, Any], public_key: str) -> bool:
    try:
        signature = _unb64(str(event["signature"]))
        Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(signature, canonical(event_without_signature(event)))
        return True
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def node_id_for_public_key(public_key: str) -> str:
    """Return the canonical SHA-256 routing ID for a raw Ed25519 public key."""

    raw_key = _unb64(public_key)
    if len(raw_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return hashlib.sha256(raw_key).hexdigest()


def validate_host_keypair(private_key: str, public_key: str) -> str:
    """Validate a configured host keypair and return its key-derived node ID."""

    private = Ed25519PrivateKey.from_private_bytes(_unb64(private_key))
    configured_public = _unb64(public_key)
    if len(configured_public) != 32 or private.public_key().public_bytes_raw() != configured_public:
        raise ValueError("Configured federation private/public keys do not match")
    return hashlib.sha256(configured_public).hexdigest()


def new_message_event(*, room_id: str, author_id: str, origin_host_id: str, body: str,
                      mentions: list[str], references: list[str], private_key: str,
                      event_id: str | None = None, created_at: str | None = None) -> dict[str, Any]:
    event = {
        "protocol": PROTOCOL_VERSION,
        "event_id": event_id or str(uuid.uuid4()),
        "room_id": room_id,
        "event_type": "message",
        "author_id": author_id,
        "origin_host_id": origin_host_id,
        "created_at": created_at or now(),
        "references": references,
        "payload": {"body": body, "mentions": mentions},
    }
    return sign_event(event, private_key)


def membership_proposal(*, room_id: str, epoch: int, hosts: list[str], quorum_m: int, quorum_n: int) -> dict[str, Any]:
    return {"room_id": room_id, "epoch": epoch, "hosts": sorted(set(hosts)), "quorum_m": quorum_m, "quorum_n": quorum_n}


def sign_membership_proposal(proposal: dict[str, Any], private_key: str) -> str:
    return _b64(Ed25519PrivateKey.from_private_bytes(_unb64(private_key)).sign(canonical(proposal)))


def verify_membership_proof(payload: dict[str, Any], public_keys: dict[str, str]) -> bool:
    try:
        proposal = payload["proposal"]
        signatures = payload["quorum_signatures"]
        if proposal["quorum_m"] < 1 or proposal["quorum_m"] > proposal["quorum_n"] or len(proposal["hosts"]) != proposal["quorum_n"]:
            return False
        seen = set()
        for item in signatures:
            signer = item["host_id"]
            if signer in seen or signer not in proposal["hosts"] or signer not in public_keys:
                continue
            Ed25519PublicKey.from_public_bytes(_unb64(public_keys[signer])).verify(_unb64(item["signature"]), canonical(proposal))
            seen.add(signer)
        return len(seen) >= proposal["quorum_m"]
    except (InvalidSignature, KeyError, TypeError, ValueError):
        return False


def valid_references(event: dict[str, Any]) -> bool:
    """References are unique, non-empty causal-parent event IDs, never the event itself."""

    references = event.get("references")
    if not isinstance(references, list) or len(references) > MAX_REFERENCES:
        return False
    if not all(isinstance(item, str) and item for item in references):
        return False
    return len(set(references)) == len(references) and event.get("event_id") not in references


def causal_order(items: list[Any], key: Callable[[Any], str], references: Callable[[Any], list[str]]) -> list[Any]:
    """Stable topological sort: parents before children, otherwise the input order.

    References to items outside ``items`` are ignored.  Items caught in a
    reference cycle (or descended from one) keep their input order at the end.
    """

    index_by_id = {key(item): index for index, item in enumerate(items)}
    children: list[list[int]] = [[] for _ in items]
    indegree = [0] * len(items)
    for index, item in enumerate(items):
        for parent in set(references(item)):
            parent_index = index_by_id.get(parent)
            if parent_index is not None and parent_index != index:
                children[parent_index].append(index)
                indegree[index] += 1
    ready = [index for index, degree in enumerate(indegree) if degree == 0]
    heapq.heapify(ready)
    emitted: list[int] = []
    while ready:
        index = heapq.heappop(ready)
        emitted.append(index)
        for child in children[index]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, child)
    done = set(emitted)
    emitted.extend(index for index in range(len(items)) if index not in done)
    return [items[index] for index in emitted]


def event_hash(event: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical(event)).hexdigest()


def configured_host() -> tuple[str, str, str]:
    host_id = os.getenv("TINYTABLE_FEDERATION_HOST_ID", "local-test-host")
    private_key = os.getenv("TINYTABLE_FEDERATION_PRIVATE_KEY", "")
    public_key = os.getenv("TINYTABLE_FEDERATION_PUBLIC_KEY", "")
    if bool(private_key) != bool(public_key):
        raise ValueError("Federation private and public keys must be configured together")
    if private_key:
        validate_host_keypair(private_key, public_key)
    return host_id, private_key, public_key
