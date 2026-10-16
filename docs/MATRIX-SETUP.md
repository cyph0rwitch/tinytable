# Matrix-first setup

For a complete installation with a built-in homeserver, use
[`SETUP.md`](SETUP.md) instead. This page describes connecting the basic
Compose setup to a homeserver you already run.

TinyTable's primary client path is a private Matrix room. The local web UI is
kept as a backup for setup, troubleshooting, and cases where a Matrix client
cannot connect.

This repository does not provide a public Matrix homeserver. Use a homeserver
you control or an account on a trusted homeserver. TinyTable connects to it
through the standard Matrix client-server API; Element is one possible client,
but any compatible Matrix client can be used.

## What runs where

```text
Matrix client -> Matrix homeserver <- matrix-bridge -> TinyTable API
                                                        |
                                                        +-- provider A adapter
                                                        +-- provider B adapter
                                                        +-- local web UI fallback
```

The bridge watches one private room. Messages from the configured operator
account become TinyTable rounds. Provider A and provider B replies are posted
back as separate Matrix users. Their Matrix display names are configurable and
do not need to match the names used by Replika, Kindroid, or another provider.
The bridge stores its sync cursor and delivery state in
`data/matrix-bridge.db`, so that file must be persistent and private.

Current provider support is limited to the separately configured Replika and
Kindroid adapters. Their credentials, browser captures, cookies,
session files, and provider-specific identifiers are never part of this
repository. If those adapters are not configured, the Matrix wiring can start
but the provider-backed channel will remain unavailable.

## Requirements

- Docker Engine with Docker Compose v2, or an equivalent Python deployment.
- A Matrix homeserver reachable over HTTPS from the machine running TinyTable.
- One Matrix account for the operator and three bot accounts: `tinytable`,
  `companion-a`, and `companion-b` (the usernames are configurable).
- A private Matrix room containing those accounts and the operator.
- Authorized provider sessions for the configured provider A and provider B if
  you want the provider-backed channel.

Keep the room private. The bridge treats every plain-text message from the
configured operator as an explicit request to share a round with the two
configured providers. Commands are `!help`, `!status`, `!preview`, `!share`,
and `!stop`.

## 1. Prepare TinyTable

```bash
git clone https://github.com/cyph0rwitch/tinytable_public.git tinytable
cd tinytable
cp .env.example .env
cp .env.matrix.example .env.matrix
chmod 600 .env .env.matrix
mkdir -p data
```

Configure `.env` with the provider settings required by the adapters. The
backend uses `TINYTABLE_KINDROID_ENABLED=1` to enable the Kindroid companion
seat; display names are configured separately (see the README's Seats
section). The TinyTable API
must have both provider connections available. Keep provider tokens and session
material in a
local secret manager or owner-readable files; do not put them in Git.

Generate a bridge token and put its digest in `.env`:

```bash
bridge_token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\n' "$bridge_token"
printf '%s\n' "$bridge_token" | sha256sum
```

Set both of these in `.env` using the SHA-256 value. The raw token belongs only
in `.env.matrix` as `TINYTABLE_OPERATOR_TOKEN`:

```dotenv
TINYTABLE_OPERATOR_TOKEN_SHA256=replace-with-bridge-token-sha256
TINYTABLE_MATRIX_BRIDGE_TOKEN_SHA256=replace-with-bridge-token-sha256
TINYTABLE_KINDROID_ENABLED=1
```

The first digest is required by the protected channel API; the second labels
the audit principal as Matrix. You may use a separate operator token for the
CLI by setting `TINYTABLE_OPERATOR_TOKEN_SHA256` to its digest and keeping the
bridge digest separate.

## 2. Create the Matrix room

Create or register the four accounts on your homeserver, then create a private
room and invite all four accounts. The bridge account must be able to read the
room and send messages. The companion A and companion B accounts only need to
send provider replies. Choose any display names that fit the personas being
connected. Record:

- homeserver URL, such as `https://matrix.example.org`;
- room ID, such as `!roomid:example.org`;
- the operator Matrix ID;
- one access token for each of the three bot accounts.

Put those values in `.env.matrix`:

```dotenv
MATRIX_HOMESERVER=https://matrix.example.org
MATRIX_ROOM_ID=!roomid:example.org
MATRIX_OPERATOR_ID=@operator:example.org
TINYTABLE_OPERATOR_NAME=Operator
TINYTABLE_REPLIKA_COMPANION_NAME=My Replika companion
TINYTABLE_KINDROID_COMPANION_NAME=My Kindroid companion
MATRIX_TINYTABLE_TOKEN=...
MATRIX_COMPANION_A_TOKEN=...
MATRIX_COMPANION_B_TOKEN=...
TINYTABLE_OPERATOR_TOKEN=...
```

Access tokens are credentials. Do not paste them into issues, screenshots, or
public documentation. Rotate them if they are ever exposed.

For an existing private homeserver, the optional
`scripts/matrix_bootstrap.py` helper can create the operator, bot accounts,
and room through the Matrix API. It requires homeserver-specific registration
tokens; it does not create or secure a homeserver for you.

## 3. Start the Matrix-first stack

```bash
docker compose -f compose.basic.yml -f compose.basic.matrix.yml \
  up --build
```

The `tinytable` service serves the API and the fallback UI. The
`matrix-bridge` service runs the Matrix sync/send loop. On first startup it
does not replay old room messages; send a new message after the bridge has
started.

Use the Matrix client as the normal interface. In the room:

```text
!help
!status
Hello both companions.
!preview What would be shared next?
!stop
```

The first ordinary message starts a table session. The bridge deliberately
does not retry an event after an uncertain provider/API result; check
`!status` before sending again.

## 4. Web UI fallback

If Matrix is unavailable, open the local fallback at
`http://127.0.0.1:8000`. Keep it bound to localhost unless you add a real
authentication and TLS boundary. The web UI and Matrix bridge use the same
SQLite database and channel state, so stop an active session in one interface
before switching to the other.

## Troubleshooting

Check the services and API health:

```bash
docker compose -f compose.basic.yml -f compose.basic.matrix.yml ps
curl http://127.0.0.1:8000/api/health
docker compose -f compose.basic.yml -f compose.basic.matrix.yml logs -f matrix-bridge
```

If Matrix receives `!status` notices but provider turns fail, the Matrix path
is working and the provider adapter or its authorization is the remaining
problem. If no Matrix messages are observed, verify the room ID, operator
Matrix ID, homeserver URL, access-token membership, and that the bridge has a
new sync cursor in `data/matrix-bridge.db`.

## Public-release boundary

Do not publish:

- `.env` or `.env.matrix`;
- `data/` or any SQLite database;
- `private/`, browser captures, HAR files, cookies, session JSON, or provider
  bootstrap material;
- live Kubernetes Secrets, Tailscale auth keys, operator tokens, or access
  tokens;
- internal rollout reports containing private hostnames or addresses.

The Windows installer is optional packaging. It is not required for the
Matrix-first deployment.
