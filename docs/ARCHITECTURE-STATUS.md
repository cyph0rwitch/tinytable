# TinyTable architecture status

Updated: 2026-10-08

This file is the source-of-truth pointer for the TinyTable work in this
checkout. TinyTable has one source tree with several historical operating
tracks; they are not separate products.

## Canonical direction: federated Commons

New design and implementation work follows the owner-led federated Commons
boundary:

- `backend/app/federation.py` and the federation endpoints in
  `backend/app/main.py` provide signed, idempotent room events.
- `docs/FEDERATION-PROTOCOL.md` defines the current signed-event protocol.
- `docs/FEDERATION-DHT-ROUTING-v0.1.md` is the future-proofing design for
  self-certifying node IDs and DHT routing. It is a proposal, not a live DHT.
- `docs/TINYTABLE-COMMONS-BRIDGE-v0.1.md` defines the read-only, provenance-
  preserving boundary to the Intelligence Commons.
- `k8s/federation-test/` is the current development overlay. Its hosts,
  identities, and storage are test-only.
- `tests/test_federation.py` and `tests/test_commons_bridge.py` are the
  acceptance gates for this direction.
- `docs/COMPANION-CHANNEL-v0.1.md` defines the operator-approved companion
  channel, its operator-token IAM boundary, and its host-local routing rule.

The separate `swarm_intelligence` checkout remains the
canonical Commons governance/core project. TinyTable must consume its bridge
contract; it must not copy governance or silently promote local conversation
into Commons authority.

## Retained foundations

The earlier message schema, SQLite migrations, provenance fields, consent
records, and provider observation code remain in the active source tree. They
are dependencies of federation and are not duplicate products to archive.

The frontend is also retained as an unfinished client surface. It is not yet
the federated Commons UI; future channel/family-chat work should be built
against the signed room and membership model rather than the old standalone
flow.

## Historical / rollback-only tracks

- `tinytable:0.1.19` is the last confirmed live standalone image.
- `tinytable:0.1.20` remains an unverified manifest reference and must not be
  treated as a confirmed rollout.
- `k8s/tinytable/` is the existing standalone live/rollback deployment
  harness. It is not the federated production deployment.
- `backend/app/standalone.html`, `run_tinytable_standalone.sh`, and
  `tinytable-standalone.service` are retained for local recovery only.
- Replika and Kindroid browser bridge code is historical/optional provider
  integration. It does not define the federated Commons architecture.

Historical rollout reports are under `archive/rollouts/`. They remain
recoverable and are not evidence that a current image is live.

## Deployment boundary

No federated Commons implementation has been rolled out to the public live
existing private TinyTable deployment. The live namespace remains the
standalone deployment until a separate, authenticated, persistent, reviewed
federated rollout is authorized.

Before that rollout, the remaining gates are persistent multi-host storage,
authenticated enrollment and revocation, verified identity/key binding,
private-room authorization, external TLS/Access validation, and a completed
rollback plan. DHT discovery remains routing only and must never grant room or
Commons authority.
