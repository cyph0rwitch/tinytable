# TinyTable Federation v0.1

TinyTable federation exchanges signed room events between explicitly invited
hosts. It is separate from Commons export, provider transport, model routing,
and DHT discovery.

## Event flow

The originating host signs a canonical JSON event. A receiving host verifies
the origin host key, room membership, participant membership, and event shape
before storing it. Event IDs are the idempotency key. Accepted events are
available through the pull endpoint and are fanned out to connected WebSocket
peers. A mention remains application data; it never invokes a model by itself.

The prototype endpoints are:

- `GET /api/federation/identity`
- `POST /api/federation/publish`
- `POST /api/federation/events`
- `GET /api/federation/events?room_id=...&after=...`
- `GET /api/federation/missing?room_id=...`
- `WS /api/federation/ws?room_id=...`

Each test host maintains a reconnecting WebSocket subscription to the other
host. Events received through that subscription are verified and stored before
being made visible locally. The pull endpoint remains the recovery path after
an outage or missed stream event.

## References and causal order

An event's `references` are its causal parents: the IDs of earlier events in
the same room that its author had seen when the event was signed. A receiving
host rejects an event whose references are not a list of at most 32 unique,
non-empty event IDs, or which reference the event itself.

An event may arrive before its parents. It is still accepted, and each parent
not yet stored for the room is recorded as missing until it arrives.
`GET /api/federation/missing` lists them with the events that reference them,
so a host can recover them through the pull path.

Hosts serve events parents-first. The pull endpoint, WebSocket backlog, and
message list apply a stable topological sort over `references` to the events
they return, falling back to arrival order (federation) or `created_at`
(messages) where the references impose no order. Neither arrival time nor an
origin host's clock may place a reply ahead of the event it references.
References to events outside the returned page do not constrain it. A
reference cycle cannot be produced honestly; events caught in one are still
served, in fallback order, after the acyclic events.

## Authority and failure behavior

Room host membership is configured as M-of-N. The first test uses 2-of-2.
Membership changes must be represented by a later signed quorum event. A
conflicting decision for one epoch is indeterminate and must fail closed; a
host must not choose a winner based on arrival time or message meaning.

The two Kubernetes hosts use separate SQLite files and Services but share a
cluster. Passing this test proves protocol behavior, not independent hosting,
NAT traversal, durable relay availability, or production key management.

## Deliberate non-goals

There is no DHT, public ingress, automatic agent invocation, Commons
replication, or production identity/key storage in v0.1.
