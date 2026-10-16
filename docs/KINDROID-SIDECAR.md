# Kindroid companion sidecar

The Kindroid companion is an opt-in private companion surface. The UI is the
same conversation shell as the Replika companion, but `/kindroid` reads only
`kindroid:companion`; the default route remains the Replika companion.

## Current safety state

The direct driver is implemented for the isolated federation-test `host-a`
workload only. The normal TinyTable deployment and `host-b` remain disabled.
The test workload must have the uncommitted `tinytable-kindroid` Secret before
the enabled flags are useful; missing or rejected credentials leave the
sidecar disconnected and fail closed.

The sidecar exposes a local contract backed by the reviewed Kindroid HTTP
transport:

- `GET /health` and `GET /status`
- `POST /ingest` for a normalized, identity-verified provider event
- `POST /send` for an explicitly confirmed outbound event

- `/send` calls Kindroid's streamed `/v1/send-message` endpoint.
- A successful Kindroid HTTP 200 without a provider message ID is recorded as
  an accepted, non-authoritative local receipt; the adapter never retries it.
- The receiver uses authenticated Firestore HTTP document polling; it does not
  use a WebSocket.
- The browser extension is not an automatic fallback; it remains opt-in and
  requires the same three-factor identity evidence.

For a Chromium HAR that contains the authenticated Firestore listener, the
local helper `tools/extract_kindroid_session.py` can derive the mounted
session and digest files without printing provider credentials:

```bash
python3 tools/extract_kindroid_session.py \
  private/kindroidhar.har private/kindroid-session
chmod 600 private/kindroid-session/*
```

The helper binds Kindroid's `sender=ai` role as the speaker identity and the
provider AI ID as the channel coordinate. This is an explicit fail-closed
choice: a received Firestore document with another sender value is rejected,
and the avatar asset URL is never used as an identity coordinate.

## API-key mode

Browser-session ID tokens expire after an hour and cannot be renewed from the
command line. When the `tinytable-kindroid-api` Secret provides
`KINDROID_API_KEY` (Kindroid General Settings, `kn_...`), the sidecar instead:

- sends with `POST /v1/send-message` and `Authorization: Bearer <key>`;
- ingests the companion's reply from that response as a provider observation,
  keyed `kindroid-api-reply:<local request id>` and labeled non-authoritative;
- does not start the Firestore receiver, so messages the companion sends outside a
  TinyTable send are not observed;
- takes `account_id`, `ai_id`, `provider_channel_id`, and `speaker_id` from
  the mounted session file (its expired tokens are ignored) and still checks
  them against the three identity digests.

In this mode the identity evidence is configuration that the operator bound,
not a provider-observed document, so it guards against a misdirected `ai_id`
rather than proving who wrote the reply. A rejected key moves the sidecar to
`auth_expired`; a timeout or 5xx after sending is reported as an unknown
outcome and is never retried.

## Identity binding

The application persists only the following digests in
`provider_identity_bindings`:

- `KINDROID_ACCOUNT_HASH`
- `KINDROID_CHANNEL_HASH`
- `KINDROID_SPEAKER_HASH`

Every Kindroid provider event must match all three. Raw session material, including
the short-lived ID-token and Firebase App Check token needed by Firestore's
HTTP channel, belongs in the uncommitted `tinytable-kindroid` Secret and
`/run/secrets/kindroid/session.json`; the main application container does not
mount that raw-session file.

## Staged enablement

1. Create `tinytable-kindroid` from `k8s/federation-test/kindroid-secret.template.yaml` using a fresh authorized capture. Keep the resulting Secret out of source control.
2. Build and deploy a new immutable TinyTable image to `host-a` only.
3. Verify `/kindroid`, `/api/runtime`, identity rejection, deduplication, SQLite persistence, streamed send acknowledgement, and Firestore receive.
4. Every UI send still requires the operator's explicit confirmation; the sidecar never retries an ambiguous outbound request.

No public Service or Ingress is added. Access remains through the existing
internal ClusterIP/SSH path.
