"""Fail-closed identity binding helpers for the Kindroid companion adapter.

Only digests cross the TinyTable/provider boundary.  The adapter may hold the
provider session in a mounted secret, but the application stores and compares
the canonical account, channel, and speaker digests instead of raw values.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping


IDENTITY_FIELDS = ("account_hash", "channel_hash", "speaker_hash")


def canonical_identity_hash(value: str) -> str:
    """Hash one canonical identity value without exposing the value itself."""

    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def normalize_identity_evidence(evidence: Mapping[str, object] | None) -> dict[str, str]:
    """Return only the three supported lower-case hexadecimal digests."""

    evidence = evidence or {}
    result: dict[str, str] = {}
    for field in IDENTITY_FIELDS:
        value = evidence.get(field)
        if isinstance(value, str):
            result[field] = value.strip().lower()
    return result


def identity_matches(expected: Mapping[str, object], evidence: Mapping[str, object] | None) -> bool:
    """Require every configured digest to match using constant-time compares."""

    observed = normalize_identity_evidence(evidence)
    for field in IDENTITY_FIELDS:
        configured = str(expected.get(field) or "").strip().lower()
        if not configured or not observed.get(field):
            return False
        if not hmac.compare_digest(configured, observed[field]):
            return False
    return True


def identity_mismatch_fields(expected: Mapping[str, object], evidence: Mapping[str, object] | None) -> list[str]:
    """Return field names that are absent or inconsistent, never raw values."""

    observed = normalize_identity_evidence(evidence)
    mismatches: list[str] = []
    for field in IDENTITY_FIELDS:
        configured = str(expected.get(field) or "").strip().lower()
        if not configured or not observed.get(field) or not hmac.compare_digest(configured, observed[field]):
            mismatches.append(field)
    return mismatches
