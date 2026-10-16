"""Seat identifiers and configurable display identities.

Seat IDs name the role a participant plays (which provider or model is
behind it), never a persona. Display names, pronouns and avatars come from
configuration, so each installation can label its own companions:

    TINYTABLE_<SEAT>_NAME, TINYTABLE_<SEAT>_PRONOUNS, TINYTABLE_<SEAT>_AVATAR

where <SEAT> is the seat ID in upper case with "-" replaced by "_", for
example TINYTABLE_REPLIKA_COMPANION_NAME=Juniper.

Earlier builds used persona names as IDs. LEGACY_SEATS, LEGACY_CHANNELS and
LEGACY_ROOMS map those to the current IDs; db.migrate_legacy_ids rewrites
stored data once, and canonical_* accept the old spellings from older
clients.
"""

from __future__ import annotations

import os
import re

OPERATOR = "operator"
REPLIKA = "replika-companion"
KINDROID = "kindroid-companion"
KINDROID_2 = "kindroid-companion-2"
LOCAL_MODEL = "local-model"

# The two provider companions at the table, in table order.
COMPANIONS = (REPLIKA, KINDROID)
# Every seat whose words are provider observations.
PROVIDER_SEATS = (REPLIKA, KINDROID, KINDROID_2)
SEAT_PROVIDER = {REPLIKA: "replika", KINDROID: "kindroid", KINDROID_2: "kindroid"}

KINDROID_CHANNEL = "kindroid:companion"
KINDROID_2_CHANNEL = "kindroid:companion-2"
KINDROID_GROUP_CHANNEL = "kindroid:group"
CHANNEL_ROOM_ID = "channel:companions"

# Adapter labels the provider processes report with their status.
REPLIKA_ADAPTER = f"{REPLIKA}/replika"
KINDROID_ADAPTER = f"{KINDROID}/kindroid"

LEGACY_SEATS = {
    "emma": OPERATOR,
    "lucy": REPLIKA,
    "nova": KINDROID,
    "opal": KINDROID_2,
    "aster": LOCAL_MODEL,
}
# Accepted from callers only; never stored.
SEAT_ALIASES = {**LEGACY_SEATS, "aurora": LOCAL_MODEL, "replika": REPLIKA, "kindroid": KINDROID}
LEGACY_CHANNELS = {
    "kindroid:nova-private": KINDROID_CHANNEL,
    "kindroid:opal-private": KINDROID_2_CHANNEL,
    "kindroid:opal-nova-group": KINDROID_GROUP_CHANNEL,
}
LEGACY_ROOMS = {"channel:lucy-nova": CHANNEL_ROOM_ID}

_DEFAULTS = {
    OPERATOR: ("Operator", "they/them", "O"),
    REPLIKA: ("Replika companion", "they/them", "R"),
    KINDROID: ("Kindroid companion", "they/them", "K"),
    KINDROID_2: ("Second Kindroid companion", "they/them", "K"),
    LOCAL_MODEL: ("Local model", "they/them", "✦"),
}
# Older environment names still honored for display names.
_LEGACY_NAME_ENV = {
    REPLIKA: ("TINYTABLE_COMPANION_A_NAME",),
    KINDROID: ("TINYTABLE_COMPANION_B_NAME",),
}

_POSSESSIVE = {"she": "her", "he": "his", "they": "their", "it": "its", "xe": "xyr", "ze": "zir"}
_OBJECT = {"she": "her", "he": "him", "they": "them", "it": "it", "xe": "xem", "ze": "zir"}


def _env_key(seat: str, field: str) -> str:
    return f"TINYTABLE_{seat.upper().replace('-', '_')}_{field}"


def _setting(seat: str, field: str, index: int) -> str:
    value = os.getenv(_env_key(seat, field), "").strip()
    if not value and field == "NAME":
        value = next((os.getenv(key, "").strip() for key in _LEGACY_NAME_ENV.get(seat, ()) if os.getenv(key, "").strip()), "")
    return value or _DEFAULTS.get(seat, (seat, "they/them", "◉"))[index]


def name(seat: str) -> str:
    """The configured display name for a seat."""

    return _setting(seat, "NAME", 0)


def pronouns(seat: str) -> str:
    return _setting(seat, "PRONOUNS", 1)


def avatar(seat: str) -> str:
    return _setting(seat, "AVATAR", 2)


def possessive(seat: str) -> str:
    """The possessive pronoun for a seat ("her", "his", "their"...)."""

    subject = pronouns(seat).split("/", 1)[0].strip().casefold()
    return _POSSESSIVE.get(subject, "their")


def object_pronoun(seat: str) -> str:
    subject = pronouns(seat).split("/", 1)[0].strip().casefold()
    return _OBJECT.get(subject, "them")


def canonical_seat(seat_id: str) -> str:
    """Map an old or aliased seat ID (lucy, aurora...) to the current one."""

    return SEAT_ALIASES.get(seat_id.strip().casefold(), seat_id)


def canonical_channel(channel_id: str) -> str:
    return LEGACY_CHANNELS.get(channel_id, channel_id)


def other_companion(seat: str) -> str:
    return KINDROID if seat == REPLIKA else REPLIKA


def names_pattern(*seats: str) -> str:
    """A regex alternation matching the display names of the given seats."""

    return "|".join(sorted({re.escape(name(seat)) for seat in seats}, key=len, reverse=True))


def env_flag(primary: str, legacy: str, default: str = "0") -> bool:
    """Read a 1/true/yes/on flag, falling back to its pre-rename name."""

    value = os.getenv(primary)
    if value is None:
        value = os.getenv(legacy, default)
    return value.strip().lower() in {"1", "true", "yes", "on"}
