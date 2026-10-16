"""Offline benchmark for how TinyTable resolves federated event reference DAGs.

Federated message events carry a ``references`` list of earlier event IDs, so a
room's history forms a DAG across hosts.  This harness generates a synthetic
two-host DAG, delivers it to ``_accept_federation_event`` under several network
orderings, and measures what the stored history looks like afterwards:

* ingest throughput through the real accept path (signature checks + SQLite)
* dangling references at acceptance time (a child accepted before its parent)
* causal-order violations in raw arrival order (``received_at``), and in the
  orders actually served by ``/api/federation/events`` and ``/api/messages``
* references still recorded as missing once every event has been delivered
* the cost of ``causal_order`` over the room's stored events

It uses a temporary SQLite database and test keys only; nothing touches the
configured database or the network.

    .venv/bin/python scripts/bench_dag_resolution.py --events 2000 --refs 2 --skew 5
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backend.app.db as database  # noqa: E402
from backend.app.db import init_db  # noqa: E402
from backend.app.federation import MAX_REFERENCES, causal_order, generate_test_keypair, new_message_event, node_id_for_public_key  # noqa: E402
from backend.app.main import _accept_federation_event, federation_events, federation_missing, messages  # noqa: E402

HOSTS = ("host-a", "host-b")
AUTHORS = {"host-a": "operator", "host-b": "replika-companion"}
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_dag(count: int, refs: int, window: int, skew_seconds: int, keys: dict, rng: random.Random) -> list[dict]:
    """Return signed events in true causal (generation) order."""

    events: list[dict] = []
    for index in range(count):
        host = HOSTS[index % 2] if rng.random() < 0.5 else HOSTS[(index + 1) % 2]
        candidates = [event["event_id"] for event in events[-window:]]
        references = rng.sample(candidates, min(refs, len(candidates)))
        # One second of true time per event; host-b's clock runs behind by skew.
        clock = BASE_TIME + timedelta(seconds=index) - timedelta(seconds=skew_seconds if host == "host-b" else 0)
        events.append(new_message_event(
            room_id="default", author_id=AUTHORS[host], origin_host_id=host, body=f"event {index}",
            mentions=[], references=references, private_key=keys[host]["private_key"],
            event_id=f"evt-{rng.getrandbits(64):016x}", created_at=_iso(clock),
        ))
    return events


def delivery_orders(events: list[dict], rng: random.Random) -> dict[str, list[dict]]:
    shuffled = events[:]
    rng.shuffle(shuffled)
    # Partition: host-b's events arrive late as one batch after host-a's.
    partitioned = [e for e in events if e["origin_host_id"] == "host-a"] + [e for e in events if e["origin_host_id"] == "host-b"]
    # Jitter: each event is delayed by up to 20 positions.
    jittered = [e for _, e in sorted(((i + rng.uniform(0, 20), e) for i, e in enumerate(events)), key=lambda pair: pair[0])]
    duplicated = events + rng.sample(events, len(events) // 4)
    rng.shuffle(duplicated)
    return {
        "in_order": events,
        "jitter_20": jittered,
        "partition": partitioned,
        "shuffled": shuffled,
        "reverse": events[::-1],
        "shuffled+dup25%": duplicated,
    }


def fresh_db(directory: str, keys: dict) -> None:
    database.DB_PATH = Path(directory) / f"bench-{time.monotonic_ns()}.db"
    init_db()
    with database.connect() as db:
        for host in HOSTS:
            public_key = keys[host]["public_key"]
            db.execute("INSERT INTO federation_hosts(host_id, domain, public_key, node_id, created_at) VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z')",
                       (host, f"{host}.test", public_key, node_id_for_public_key(public_key)))
            db.execute("INSERT INTO federation_rooms(room_id, host_id, quorum_m, quorum_n) VALUES ('default', ?, 1, 2)", (host,))


def violations(order: list[str], parents: dict[str, list[str]]) -> tuple[int, int]:
    """Return (edges where a child precedes its parent, events with any such edge)."""

    position = {event_id: index for index, event_id in enumerate(order)}
    bad_edges = 0
    bad_events = 0
    for child, refs in parents.items():
        broken = sum(1 for parent in refs if parent in position and position[child] < position[parent])
        bad_edges += broken
        bad_events += bool(broken)
    return bad_edges, bad_events


def run_scenario(name: str, delivery: list[dict], parents: dict[str, list[str]], directory: str, keys: dict) -> dict:
    fresh_db(directory, keys)
    seen: set[str] = set()
    dangling_edges = 0
    dangling_events = 0
    started = time.perf_counter()
    for event in delivery:
        result = _accept_federation_event(event)
        if result["duplicate"]:
            continue
        missing = sum(1 for parent in event["references"] if parent not in seen)
        dangling_edges += missing
        dangling_events += bool(missing)
        seen.add(event["event_id"])
    ingest_seconds = time.perf_counter() - started

    with database.connect() as db:
        arrival = [json.loads(row["event_json"]) for row in db.execute(
            "SELECT event_json FROM federation_events WHERE room_id='default' ORDER BY received_at, event_id")]

    started = time.perf_counter()
    causal_order(arrival, lambda event: event["event_id"], lambda event: event["references"])
    resolve_ms = (time.perf_counter() - started) * 1000

    served = [event["event_id"] for event in federation_events(room_id="default", limit=2000)["events"]]
    ui_order = [message["id"] for message in messages(room_id="default", limit=2000) if message["provider"] == "federation"]

    return {
        "scenario": name,
        "delivered": len(delivery),
        "stored": len(arrival),
        "ingest_eps": len(delivery) / ingest_seconds,
        "dangling_at_accept": dangling_events,
        "dangling_edges": dangling_edges,
        "arrival_viol": violations([e["event_id"] for e in arrival], parents),
        "served_viol": violations(served, parents),
        "ui_viol": violations(ui_order, parents),
        "missing_after": len(federation_missing(room_id="default")["missing"]),
        "resolve_ms": resolve_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--refs", type=int, default=2, help="references per event")
    parser.add_argument("--window", type=int, default=8, help="how far back references may point")
    parser.add_argument("--skew", type=int, default=0, help="seconds host-b's clock runs behind")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="emit raw results as JSON")
    args = parser.parse_args()
    if not 1 <= args.events <= 2000:
        parser.error("--events must be between 1 and 2000 (the served endpoints' page limit)")
    if not 0 <= args.refs <= MAX_REFERENCES:
        parser.error(f"--refs must be between 0 and {MAX_REFERENCES}")

    rng = random.Random(args.seed)
    keys = {host: generate_test_keypair(host.encode()) for host in HOSTS}
    events = build_dag(args.events, args.refs, args.window, args.skew, keys, rng)
    parents = {event["event_id"]: event["references"] for event in events}
    edges = sum(len(refs) for refs in parents.values())

    original_path = database.DB_PATH
    results = []
    try:
        with TemporaryDirectory() as directory:
            for name, delivery in delivery_orders(events, rng).items():
                results.append(run_scenario(name, delivery, parents, directory, keys))
    finally:
        database.DB_PATH = original_path

    if args.json:
        print(json.dumps({"params": vars(args), "edges": edges, "results": results}, indent=2))
        return 0

    print(f"DAG: {args.events} events, {edges} reference edges, refs={args.refs}, window={args.window}, skew={args.skew}s, seed={args.seed}")
    print("violations = child-before-parent edges / events affected\n")
    header = f"{'scenario':<16}{'stored':>7}{'ingest ev/s':>12}{'dangling@accept':>16}{'arrival viol':>14}{'served viol':>12}{'UI viol':>9}{'missing':>8}{'sort ms':>9}"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['scenario']:<16}{r['stored']:>7}{r['ingest_eps']:>12.0f}{r['dangling_at_accept']:>16}"
              f"{'%d/%d' % r['arrival_viol']:>14}{'%d/%d' % r['served_viol']:>12}{'%d/%d' % r['ui_viol']:>9}{r['missing_after']:>8}{r['resolve_ms']:>9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
