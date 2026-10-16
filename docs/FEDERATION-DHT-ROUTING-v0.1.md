# TinyTable DHT Routing — design proposal v0.1

This is a design note, not an implementation. It describes how a Kademlia
discovery/routing layer could attach to the existing federation substrate
(`FEDERATION-PROTOCOL.md`). Nothing here is wired yet.

The guiding separation: **routing is not authority.** Kademlia answers "which
hosts can I reach and which are responsible for a key." It must never decide
room membership or event validity — that stays with the signed M-of-N quorum
that federation v0.1 already enforces. The DHT only replaces today's hardcoded
`peers` list with learned, verifiable peers.

## What already fits

The federation layer gives us the hard parts a DHT needs:

- Stable host identity: Ed25519 keypair per host (`federation.py`).
- Addressable, signed, idempotent events with an outage-recovery pull path.
- A reconnecting peer loop (`_federation_peer_loop`) that already turns a URL
  into a live subscription — the natural consumer of routing output.
- `federation_hosts` / `federation_rooms` tables as the trust record.

## The one blocking mismatch: node IDs

Today `host_id` is a human-readable config string (`configured_host()`), so
there is no metric space to route over. Kademlia needs node IDs that live in
the same space as keys.

Proposal: derive the node ID from the host's public key.

    node_id = SHA256(ed25519_public_key)   # 256-bit, matches key space

Persist this as a canonical lowercase 64-character hex value alongside the
trusted public key (`federation_hosts.node_id`), but never treat the stored
value as authority by itself. Recompute it from the decoded 32-byte Ed25519
public key whenever a host record is loaded at a trust boundary and reject a
row whose stored hash does not match. The local host must also prove that its
configured private key derives the configured public key before it can sign.

This makes identity, signature, and routing position the same fact. A host
cannot cheaply choose its node ID (it would have to grind keypairs), and the
ID is self-certifying: a peer verifies that a claimed node ID matches the
pubkey it already checks for signatures. `host_id` stays as the friendly label;
`node_id` becomes the routing coordinate. XOR over these hashes is the distance
metric.

Keep the friendly `host_id` bound to that key in the trust registry; do not
accept a peer-provided key as a replacement for the key already trusted under
that label. Check the binding at each of these points:

1. **Local startup:** decode both configured keys, derive the public key from
   the private key, compare it byte-for-byte with the configured public key,
   then derive and persist `node_id`.
2. **Identity response:** recompute the advertised `node_id` from the returned
   public key and compare it with the stored trusted-host record before
   learning the contact. A self-asserted hash is never sufficient.
3. **Signed RPC or event:** resolve `origin_host_id` to an active trusted row,
   recompute that row's node ID from its public key, compare any signed
   `origin_node_id`, then verify the signature with that same key.
4. **Room authorization:** still require the existing signed quorum
   membership. In a protocol revision that carries node IDs in membership
   proofs, verify each `(host_id, node_id, public_key)` tuple against the
   trust registry before counting its signature.

The hash is a stable routing coordinate, not a human identity or consent
signal. A key rotation creates a new node ID and must go through explicit key
revocation and a newer signed membership decision; startup must not silently
replace a trusted key under an existing `host_id`.

Alternative considered: keep string `host_id`s and hash them into the keyspace.
Rejected — it decouples routing position from the key we already authenticate,
so a host could claim a convenient position without owning the matching key.

## Layering

Four layers, bottom to top. Only the top one is new behavior; the rest is
plumbing around existing primitives.

1. **Transport** — the signed-event endpoints and WS fan-out, unchanged.
2. **Node table** — a routing table of k-buckets keyed by XOR distance from our
   `node_id`. Entries are `(node_id, url, public_key, last_seen)`. This is
   learned state, distinct from `federation_hosts` (which is the *trust*
   record); a node in the table is reachable, not necessarily authorized.
3. **DHT RPCs** — `PING`, `FIND_NODE`, and optionally `FIND_VALUE` / `STORE`.
   Every RPC is a signed federation event (reuse `sign_event` / `verify_event`,
   new `event_type`s), so discovery inherits the same authentication and the
   same replay/idempotency handling. No new crypto path.
4. **Peer-loop driver** — `_federation_peer_loop` stops reading a static list
   and instead asks the node table for the peers closest to each joined room's
   key, then subscribes to them. Bootstrap stays explicit: a small configured
   seed list of known hosts to join the network through.

## How routing meets quorum

A room has a key (`SHA256(room_id)`, or a key carried in its membership event).
`FIND_NODE` against that key yields candidate hosts *to talk to*. Membership is
then decided exactly as today: a host is a valid room host only if the signed
M-of-N quorum event says so. So the flow is:

    FIND_NODE(room_key) -> candidate peers      (routing: who's reachable)
    verify_membership_proof(...)                (authority: who's allowed)
    subscribe + pull signed events              (existing federation path)

A node discovered via the DHT that is not in the room's quorum can still relay
and be a routing neighbor, but its events are rejected by the existing host
allow-list (`_federation_host_allowed`). Routing failures degrade to the
current behavior — fall back to configured seeds — rather than failing closed,
because reachability is not a safety property; authority already fails closed.

## RPC envelope

Sketched as a JSON Schema in `backend/app/dht_protocol_schema.json`, in the
style of `commons_protocol_schema.json`.

A DHT RPC is a *sibling* of the room-event envelope, not a room event. The
current validator (`_accept_federation_event`, `main.py`) requires an exact
10-key set and only `event_type ∈ {message, membership_change}`, so RPCs cannot
reuse that path without loosening it — and they shouldn't, because a `PING` or
`FIND_NODE` has no `room_id` and no `author_id` and must confer no authority.
So the RPC gets its own `protocol` (`tinytable-dht-v0.1`), its own accept path,
and its own fields, while reusing `sign_event` / `verify_event` unchanged.

The envelope: `protocol`, `rpc_id` (replay key), `rpc_type`
(`ping` / `pong` / `find_node` / `find_node_result`), `origin_host_id`,
`origin_node_id`, `created_at`, `payload`, `signature`. The `origin_node_id` is
self-certifying — the receiver recomputes `SHA256(public_key)` from the pubkey
it already verifies for the signature and rejects a mismatch, so a host cannot
claim a routing position it does not own. The commons schema already reserves a
`FederationNodeIdentity` object type; a persisted node-identity record should
align with that rather than invent a second shape.

`find_node` carries a single `target` key; `find_node_result` returns up to k
closest `contact`s (`node_id`, `host_id`, `url`, `public_key`) from the
responder's table. `target` is a node_id or a keyspace key
(`SHA256("room:"+room_id)` / `SHA256("commons:"+record_id)`), which is where the
next section comes in.

## Commons keyspace

Open question, not yet decided: whether Commons records route over this same
DHT or a separate keyspace. Recommendation: **separate namespace, same metric.**
Commons has a different consent boundary (`TINYTABLE-COMMONS-BRIDGE-v0.1.md`)
and should not share a key with room events, but it can reuse the same node
table and RPCs. Prefix the key input (`"room:" + room_id` vs
`"commons:" + record_id`) so the two never collide while routing code stays one
implementation.

## Accept path (pseudocode)

A sketch, not drop-in code. It mirrors `_accept_federation_event`'s idioms
(`connect()`, `verify_event`, the `federation_hosts` row) but lives on its own
path because a DHT RPC is network-layer, not a room event. The ordering matters:
cheap rejects first, signature before any state change, and **routing never
fails closed** — a bad or unreachable peer is dropped, not escalated.

    def accept_dht_rpc(rpc: dict) -> dict:
        # 1. Shape — exact key set + protocol, per dht_protocol_schema.json.
        if not valid_shape(rpc) or rpc["protocol"] != "tinytable-dht-v0.1":
            drop("malformed")                      # no DB touch, no error to peer

        # 2. Known host — must be an active federation_hosts row. Routing reuses
        #    the trust record for *authentication*; it does not grant authority.
        host = db.row("SELECT public_key FROM federation_hosts "
                      "WHERE host_id=? AND status='active'", rpc["origin_host_id"])
        if not host:
            drop("unknown host")

        # 3. Self-certifying node id — recompute from the pubkey we already trust.
        if rpc["origin_node_id"] != sha256_hex(unb64(host.public_key)):
            drop("node_id does not match host key")

        # 4. Signature — same Ed25519 path as room events, over the RPC envelope.
        if not verify_event(rpc, host.public_key):
            drop("bad signature")

        # 5. Replay — rpc_id dedup with a TTL. RPCs are high-volume and ephemeral,
        #    so unlike federation_events this is a bounded cache, not a durable log.
        if seen_recently(rpc["rpc_id"]):
            return {"duplicate": True}
        remember(rpc["rpc_id"], ttl=RPC_REPLAY_TTL)

        # 6. The sender is now a verified, reachable contact — learn it before
        #    answering, so even a PING improves the table.
        touch_contact(node_id=rpc["origin_node_id"], host_id=rpc["origin_host_id"],
                      url=sender_url(rpc), public_key=host.public_key)

        # 7. Dispatch.
        if rpc["rpc_type"] == "ping":
            return make_signed_rpc("pong", payload={})
        if rpc["rpc_type"] == "find_node":
            target = rpc["payload"]["target"]
            return make_signed_rpc("find_node_result",
                                   payload={"target": target,
                                            "nodes": closest_k(target, K)})
        if rpc["rpc_type"] in ("pong", "find_node_result"):
            for c in rpc["payload"].get("nodes", []):
                if c["node_id"] == sha256_hex(unb64(c["public_key"])):  # re-verify
                    touch_contact(**c)             # learned, unverified until pinged
            return {"ok": True}

`closest_k(target, K)` is XOR distance over the hex node_ids, taking the K
nearest live contacts from the routing table.

`touch_contact(...)` maintains the k-buckets:

    def touch_contact(node_id, host_id, url, public_key):
        bucket = bucket_index(xor(our_node_id, node_id))   # leading-zero count
        if node_id in bucket:
            bucket.move_to_tail(node_id); bucket.update(last_seen=now())
        elif len(bucket) < K:
            bucket.append(node_id, host_id, url, public_key, last_seen=now())
        else:
            # Kademlia eviction: probe the least-recently-seen entry; keep it if
            # it still answers (long-lived nodes are preferred), else replace it.
            oldest = bucket.head()
            if ping_succeeds(oldest):
                bucket.move_to_tail(oldest)        # incumbent wins, new node dropped
            else:
                bucket.replace(oldest, node_id, host_id, url, public_key)

Two properties to hold onto: contacts learned from a `find_node_result` are
*unverified* — a third party vouched for them — so they are candidates to ping,
never trusted until they answer directly and pass steps 2–4 themselves. And the
table is reachability state only; a contact in it is still subject to the room's
M-of-N quorum before any of its *room events* are accepted. The two checks never
merge.

## Deliberate non-goals for a first DHT slice

- No public ingress or NAT traversal — same internal-only posture as v0.1.
  Solve the authenticated private access path (Tailscale/SSH/reverse proxy)
  and durable storage *first*; those are the real blockers from the Oct 8 test.
- No value storage (`STORE`/`FIND_VALUE`) in the first slice — node discovery
  (`FIND_NODE` only) is enough to replace the static peer list and is far
  easier to reason about. Add value storage only if a concrete need appears.
- No automatic agent invocation, and no change to how mentions work.
- No eclipse/Sybil hardening beyond key-derived IDs in this slice; note it as a
  known gap for a networked (non-LAN) deployment.

## Smallest useful first step

Add `node_id = SHA256(public_key)` to the identity endpoint and a read-only
`/api/federation/nodes` view of a seeded node table, with `PING` and
`FIND_NODE` as signed events — no change yet to `_federation_peer_loop`. That
proves ID derivation, the k-bucket table, and signed RPCs against the running
two-host test before anything depends on the DHT for peer selection.
