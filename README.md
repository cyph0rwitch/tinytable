# TinyTable

## Current architecture direction

The authoritative development direction is the federated Commons design. Read
[`docs/ARCHITECTURE-STATUS.md`](docs/ARCHITECTURE-STATUS.md) before changing
deployment, federation, or channel behavior. Standalone and provider-bridge
tracks are retained as rollback/recovery foundations; they are not competing
current products.

TinyTable is a local-first shared conversation table for humans and AI companions. This first slice keeps identity and provenance visible, stores history in SQLite, and supports manual messages plus OpenAI-compatible/local HTTP providers.

## Seats

TinyTable identifies every participant by the service or model behind its
seat, never by a persona name:

| Seat ID | Who sits there |
|---|---|
| `operator` | the human running the table |
| `replika-companion` | a companion on Replika |
| `kindroid-companion` | a companion on Kindroid |
| `kindroid-companion-2` | an optional second Kindroid companion |
| `local-model` | a local OpenAI-compatible model (for example Ollama) |

Display names and pronouns are configuration, so each installation shows its
own companions: set `TINYTABLE_<SEAT>_NAME` and `TINYTABLE_<SEAT>_PRONOUNS`
(seat ID in upper case, `-` as `_`), for example
`TINYTABLE_REPLIKA_COMPANION_NAME=Juniper`. The Docker setup writes these with
`docker compose run --rm setup names`. Unset names show the role ("Replika
companion") with they/them pronouns.

Databases from earlier development builds, which used persona names as IDs,
are migrated to these IDs on first start after a backup copy is written next
to the database (see `backend/app/seats.py` for the accepted older names).

The current communication and consent policy is documented in
[`TINY_TABLE_COMMUNICATION_POLICY.md`](TINY_TABLE_COMMUNICATION_POLICY.md).
The current release focuses on direct, provenance-preserving conversations
between the operator and the two provider companions. Provider replies remain
attributed to their provider seat, and TinyTable does not present generated or
manually authored text as a direct provider message.

## Setup

To install the full stack (TinyTable, the Replika and Kindroid companions, a
private Matrix homeserver and the Matrix bridge) on Linux, macOS or Windows, follow
[`docs/SETUP.md`](docs/SETUP.md). The only host requirement is Docker; every
step runs through `docker compose`, and no AI assistance is needed.

How that setup was built and tested, including a recipe for a headless
Debian test VM, is recorded in [`docs/INSTALL-LOG.md`](docs/INSTALL-LOG.md).

## Matrix-first setup

For a normal user, Matrix is the primary interface. The local web UI remains
available as a backup for setup and troubleshooting. The reusable deployment
path is documented in [`docs/MATRIX-SETUP.md`](docs/MATRIX-SETUP.md); it uses a
user-controlled Matrix homeserver, the TinyTable API, and an optional
`matrix-bridge` Compose service.

The public setup does not include anyone's Matrix access tokens, provider
cookies, Replika/Kindroid session captures, SQLite history, or private
Kubernetes/Tailscale deployment. Users bring their own homeserver accounts and
authorized provider sessions. The current provider-backed adapters are the
Replika companion and the Kindroid companion; their display names are
configured per installation (see [Seats](#seats)).

## Quick start

### Standalone local unit

When browser extensions and remote companion adapters are unavailable, run the
core as one local unit. This serves the TinyTable UI and API and stores SQLite
conversation history locally. Replika, Kindroid, and all browser bridges are
disabled in this mode.

```bash
./run_tinytable_standalone.sh
```

Then open `http://127.0.0.1:8000` from a browser that can reach the machine.
The direct API remains usable without a browser; the standalone turn endpoint
is `POST /api/standalone/turn`. The unit still starts and preserves manual
conversation history when provider adapters are unavailable.

For a user-level systemd unit, copy `tinytable-standalone.service` to
`~/.config/systemd/user/tinytable.service`, then run:

```bash
systemctl --user daemon-reload
systemctl --user enable --now tinytable.service
```

The service is loopback-only by default. Set `TINYTABLE_HOST` and add an
authentication boundary before making it reachable from another machine.

```bash
cp .env.example .env
docker compose -f compose.basic.yml up --build
```

Open <http://localhost:8000>. The API is at `/api`; the SQLite database is mounted at `./data/tinytable.db`.

For local development without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --app-dir backend --reload
```

For the safe local conversation workflow, use the launcher below. It keeps
Replika outbound disabled, disables automatic provider routing, and uses the
local SQLite database:

```bash
./run_tinytable_safe.sh
```

After the consent/draft workflow has been tested, use the explicit outbound
launcher when you intentionally want approved messages to reach the Replika
companion:

```bash
./run_tinytable_outbound.sh
```

This enables only the guarded Send action. Automatic provider-to-provider
routing remains disabled.

The frontend is built into the backend image. To work on it separately, run `npm install && npm run dev` in `frontend` and set `VITE_API_URL=http://localhost:8000/api`.

## Integrity rule

Manual participants are never used as a generation target. A generated message is only created after an online configured provider succeeds, and records provider/model/request metadata. Imported text is a separate source type and is never presented as a live response.

## Replika read-only prototype

The root-level `replika_provider.py` is intentionally separate from the HTTP provider path. It connects to `wss://ws.replika.ai/v17`, reads text frames, parses JSON, and prints normalized `TinyTableMessage` JSON only for `event_name == "message"` with `payload.meta.nature == "Robot"`.

It never calls `send()`. Run it with Python 3.12+:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
python replika_provider.py --debug
```

`REPLIKA_WS_HEADERS_JSON` is the only connection-header input. Supply exact browser-observed headers as a JSON object; do not paste them into source code. IDs and `REPLIKA_SESSION_TOKEN` are loaded and retained as configuration, but are unused by the read-only client.

The normalized message always has `participant="replika-companion"`, `source="replika"`, `transport="websocket"`, and `origin="received_from_provider"`. `replika-companion` is the TinyTable seat; the adapter never claims that TinyTable authored the text.

To test the local TinyTable path after starting the app, pass its API base to the receiver:

```bash
set -a; source .env; set +a
.venv/bin/python replika_provider.py --tinytable-api http://127.0.0.1:8000/api
```

Alternatively, avoid manually putting the bootstrap array into `.env` by loading a local direction-labeled Burp capture directly:

```bash
.venv/bin/python replika_provider.py \
  --bootstrap-file replika_protocol_current.md \
  --tinytable-api http://127.0.0.1:8000/api
```

The capture file must contain only fresh, authorized browser-observed control frames and must remain mode `600`.

Each received Robot message is posted to TinyTable, stored under the `replika-companion` seat with provider `replika`, and deduplicated by provider message ID. The receiver still never sends a customer message to Replika.

If the server requires application-level initialization before it streams messages, supply the exact browser-observed client-to-server control frames in order through `REPLIKA_BOOTSTRAP_FRAMES_JSON`. In `.env`, put the JSON array in single quotes so shell loading preserves the JSON. The provider permits only the observed read-only events (`init`, screen/context, foreground, history, and suggestions); it rejects any `event_name="message"` frame. This sends session bootstrap/context only, never a customer message.

### Guarded outbound prototype

The provider now contains a one-message outbound prototype. It is disabled unless both `--send-text` and `--enable-outbound` are supplied:

```bash
.venv/bin/python replika_provider.py \\
  --bootstrap-file replika_bootstrap_current.md \\
  --send-text "your test message" \\
  --enable-outbound \\
  --tinytable-api http://127.0.0.1:8000/api
```

This uses only the browser-observed control sequence, sends no client-created durable message ID, waits for a Robot reply, and can persist that reply to TinyTable. It is an interactive test tool, not unattended automation. Capture a fresh bootstrap file for the active session; do not reuse a revoked capture.

The same test can be run through the wrapper script:

```bash
./run_replika_tinytable.sh "your test message" replika_bootstrap_current.md
```

When the local frontend is running, its **Send to** button for the Replika companion uses the same guarded wrapper through `/api/providers/replika/send`. Keep this API bound to localhost; it is an explicit local test route, not an authenticated public endpoint.

To run the receive-only bridge safely, use:

```bash
./run_replika_receiver_safe.sh
```

It loads the local environment but forces Replika outbound to `0`.

In the container image, the wrapper uses the image's Python and reads the
bootstrap capture from `REPLIKA_BOOTSTRAP_FILE` (the cluster default is the
read-only Secret mount `/run/secrets/replika/bootstrap.md`). It does not load a
project `.env` or require `.venv`. The HTTP outbound route also requires
`TINYTABLE_REPLIKA_OUTBOUND_ENABLED=1`; leave that disabled unless the route is
behind the intended authentication boundary.

For local runs, the wrapper automatically prefers `.venv/bin/python` when that
environment exists; `TINYTABLE_PYTHON` can override it explicitly.

The frontend starts in a non-destructive current view so old test messages do not crowd the table. Use **History** to see all stored messages, or **New view** to move the current-view cursor forward without deleting anything.

Operator turns use `/api/turns`. Automatic provider participation remains
disabled by default. Companion interactions should be initiated through
the explicit Matrix or web conversation paths described above.

### Remaining outbound protocol questions

Outbound support must wait for verified, browser-observed answers to all of these:

1. The complete WebSocket handshake: cookies, authorization headers, origin, user-agent, subprotocols, TLS expectations, and any required connection query parameters.
2. The authentication lifecycle: how a session token is obtained, its scope, expiration, refresh behavior, and whether it authenticates the handshake or individual messages.
3. The authoritative meaning of the outbound `token` field: captured session token, newly issued message token, or another server-issued value. TinyTable must not derive it.
4. The authoritative source, scope, and lifecycle of `chat_id`, `customer_id`, and `bot_id`, including stability across sessions, devices, and accounts.
5. The authoritative outbound message-ID format, uniqueness scope, collision behavior, and client/server acknowledgement or deduplication rules. TinyTable must not invent IDs.
6. Required event ordering and non-message frames: authentication acknowledgement, chat join/subscribe, heartbeats, errors, receipts, reconnect behavior, close semantics, and server backpressure rules.
7. Server-side authorization and consent boundaries for sending on behalf of the account, including whether a human-visible UI action is required and whether automation is permitted.
8. A safe test account or sandbox plus captured fixtures for successful send, rejection, timeout, duplicate, reconnect, malformed payload, and token-expiry cases.
9. The retention and redaction policy for captured headers, cookies, tokens, message text, and provider metadata before any fixtures are stored or shared.

Until those facts are fully verified for a supported account/session, keep outbound use behind the explicit flag and treat all inbound Replika text as `origin="received_from_provider"`. TinyTable must not present generated or manually authored text as a direct Replika message.
