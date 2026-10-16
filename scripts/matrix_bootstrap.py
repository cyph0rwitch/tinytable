#!/usr/bin/env python3
"""One-time setup of a Matrix room for two configurable provider seats.

Registers the operator, the TinyTable bot, and two configurable companion
accounts; creates a private table room; and writes the generated access tokens
to a local mode-600 env file. Bot passwords are random and never shown.

The homeserver only accepts the configured registration token after its
first account exists. The first account needs the one-time token supplied by
the homeserver administrator.

Usage (with the homeserver reachable):
    MATRIX_FIRST_TOKEN=... MATRIX_REGISTRATION_TOKEN=... scripts/matrix_bootstrap.py \\
        --homeserver https://matrix.example.org \\
        --operator-username operator \\
        --companion-a-username companion-a \\
        --companion-b-username companion-b \\
        --output-env .env.matrix
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

DISCLOSURE = (
    "TinyTable table with two provider seats. Provider replies are exact copies of what the configured "
    "providers said, relayed by TinyTable. Everything the operator writes here is shared with both seats."
)


class MatrixError(Exception):
    pass


def call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    request = urllib.request.Request(f"{base}/_matrix/client/v3{path}", data=json.dumps(body).encode() if body is not None else None, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read() or b"{}")
        if exc.code == 401 and "flows" in payload:
            return payload  # user-interactive auth: the caller completes the stages
        raise MatrixError(f"{method} {path}: {exc.code} {payload.get('errcode')} {payload.get('error')}") from exc


def register(base: str, username: str, password: str, registration_token: str) -> dict:
    body = {"username": username, "password": password, "inhibit_login": False, "initial_device_display_name": "tinytable bootstrap"}
    result = call(base, "POST", "/register", body)
    # Complete whichever stages the server asks for: the token, then a dummy stage.
    for _ in range(4):
        if "access_token" in result:
            return result
        if result.get("errcode"):
            raise MatrixError(f"Registering {username}: {result['errcode']} {result.get('error')}")
        completed = set(result.get("completed", []))
        stages = next((flow["stages"] for flow in result["flows"] if set(flow["stages"]) <= {"m.login.registration_token", "m.login.dummy"}), None)
        if not stages:
            raise MatrixError(f"Unsupported registration flows: {result['flows']}")
        stage = next(stage for stage in stages if stage not in completed)
        auth = {"type": stage, "session": result.get("session")}
        if stage == "m.login.registration_token":
            auth["token"] = registration_token
        result = call(base, "POST", "/register", {**body, "auth": auth})
    raise MatrixError(f"Registration of {username} did not complete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--homeserver", default=os.getenv("MATRIX_HOMESERVER"))
    parser.add_argument("--operator-username", default=os.getenv("MATRIX_OPERATOR_USERNAME", "operator"))
    parser.add_argument("--operator-name", default=os.getenv("TINYTABLE_OPERATOR_NAME", "Operator"))
    parser.add_argument("--tinytable-username", default=os.getenv("MATRIX_TINYTABLE_USERNAME", "tinytable"))
    parser.add_argument("--companion-a-username", default=os.getenv("MATRIX_COMPANION_A_USERNAME", "companion-a"))
    parser.add_argument("--companion-b-username", default=os.getenv("MATRIX_COMPANION_B_USERNAME", "companion-b"))
    parser.add_argument("--companion-a-name", default=os.getenv("TINYTABLE_REPLIKA_COMPANION_NAME") or os.getenv("TINYTABLE_COMPANION_A_NAME", "Replika companion"))
    parser.add_argument("--companion-b-name", default=os.getenv("TINYTABLE_KINDROID_COMPANION_NAME") or os.getenv("TINYTABLE_COMPANION_B_NAME", "Kindroid companion"))
    parser.add_argument("--room-alias", default=os.getenv("MATRIX_ROOM_ALIAS", "table"))
    parser.add_argument("--room-name", default=os.getenv("MATRIX_ROOM_NAME", "TinyTable"))
    parser.add_argument("--output-env", type=Path, default=Path(".env.matrix"),
                        help="mode-600 file for the generated bridge configuration")
    parser.add_argument("--digest-env", type=Path, default=None,
                        help="also write the bridge token's SHA-256 digests to this mode-600 env file")
    parser.add_argument("--client-url", default=None,
                        help="homeserver URL to show for signing in, if clients use a different address")
    args = parser.parse_args()
    if not args.homeserver:
        print("Set --homeserver or MATRIX_HOMESERVER.", file=sys.stderr)
        return 1
    base = args.homeserver.rstrip("/")
    registration_token = os.getenv("MATRIX_REGISTRATION_TOKEN", "").strip()
    first_token = os.getenv("MATRIX_FIRST_TOKEN", "").strip()
    if not registration_token or not first_token:
        print("Set MATRIX_REGISTRATION_TOKEN (the REGISTRATION_TOKEN in the tinytable-matrix Secret) and "
              "MATRIX_FIRST_TOKEN (the one-time token in the homeserver's startup log).", file=sys.stderr)
        return 1
    password = getpass.getpass(f"Choose {args.operator_username}'s Matrix password: ")
    if len(password) < 12 or password != getpass.getpass("Again: "):
        print("Passwords must match and be at least 12 characters.", file=sys.stderr)
        return 1

    bot_accounts = {
        "tinytable": (args.tinytable_username, "TinyTable"),
        "replika": (args.companion_a_username, f"{args.companion_a_name} (Replika · relayed exactly)"),
        "kindroid": (args.companion_b_username, f"{args.companion_b_name} (Kindroid · relayed exactly)"),
    }
    try:
        operator = register(base, args.operator_username, password, first_token)
        tokens = {seat: register(base, username, secrets.token_urlsafe(32), registration_token) for seat, (username, _) in bot_accounts.items()}
        for seat, (_, display) in bot_accounts.items():
            user = tokens[seat]["user_id"]
            call(base, "PUT", f"/profile/{urllib.parse.quote(user)}/displayname", {"displayname": display}, tokens[seat]["access_token"])
        ids = {seat: tokens[seat]["user_id"] for seat in bot_accounts} | {"operator": operator["user_id"]}
        room = call(base, "POST", "/createRoom", {
            "preset": "private_chat",
            "name": args.room_name,
            "topic": DISCLOSURE,
            "room_alias_name": args.room_alias,
            "invite": [ids["operator"], ids["replika"], ids["kindroid"]],
            # Only the operator and the bridge's three accounts can post; nobody else is invited.
            "power_level_content_override": {
                "users": {ids["tinytable"]: 100, ids["operator"]: 100, ids["replika"]: 50, ids["kindroid"]: 50},
                "users_default": 0,
                "events_default": 50,
                "invite": 100,
            },
        }, tokens["tinytable"]["access_token"])["room_id"]
        quoted = urllib.parse.quote(room)
        for name in ("replika", "kindroid"):
            call(base, "POST", f"/rooms/{quoted}/join", {}, tokens[name]["access_token"])
        call(base, "POST", f"/rooms/{quoted}/join", {}, operator["access_token"])
        # The setup session is not kept; the operator signs in from their client.
        call(base, "POST", "/logout", {}, operator["access_token"])
    except MatrixError as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1

    bridge_token = secrets.token_urlsafe(32)
    values = {
        "MATRIX_HOMESERVER": base,
        "MATRIX_ROOM_ID": room,
        "MATRIX_OPERATOR_ID": ids["operator"],
        "TINYTABLE_OPERATOR_NAME": args.operator_name,
        "TINYTABLE_COMPANION_A_NAME": args.companion_a_name,
        "TINYTABLE_COMPANION_B_NAME": args.companion_b_name,
        "MATRIX_TINYTABLE_TOKEN": tokens["tinytable"]["access_token"],
        "MATRIX_COMPANION_A_TOKEN": tokens["replika"]["access_token"],
        "MATRIX_COMPANION_B_TOKEN": tokens["kindroid"]["access_token"],
        "TINYTABLE_OPERATOR_TOKEN": bridge_token,
    }
    try:
        args.output_env.parent.mkdir(parents=True, exist_ok=True)
        with args.output_env.open("x", encoding="utf-8") as handle:
            handle.write("# Generated by scripts/matrix_bootstrap.py; keep mode 600.\n")
            for key, value in values.items():
                handle.write(f"{key}={value}\n")
        os.chmod(args.output_env, 0o600)
    except FileExistsError:
        print(f"Refusing to overwrite existing {args.output_env}; remove it only after reviewing its contents.", file=sys.stderr)
        return 1
    print(f"\nRoom {room} is ready. Sign in to your Matrix client at {args.client_url or base} as {ids['operator']}.")
    print(f"Bridge configuration written to {args.output_env} with mode 600.")
    digest = hashlib.sha256(bridge_token.encode()).hexdigest()
    if args.digest_env:
        args.digest_env.write_text(
            "# Generated by scripts/matrix_bootstrap.py; digests of the bridge token in the bridge env file.\n"
            f"TINYTABLE_OPERATOR_TOKEN_SHA256={digest}\nTINYTABLE_MATRIX_BRIDGE_TOKEN_SHA256={digest}\n",
            encoding="utf-8",
        )
        os.chmod(args.digest_env, 0o600)
        print(f"Bridge token digests written to {args.digest_env} with mode 600.")
        return 0
    print("Put the SHA-256 digest of TINYTABLE_OPERATOR_TOKEN in the TinyTable service environment as:")
    print(f"TINYTABLE_OPERATOR_TOKEN_SHA256={digest}")
    print(f"TINYTABLE_MATRIX_BRIDGE_TOKEN_SHA256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
