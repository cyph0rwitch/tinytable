import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import backend.app.db as database
from backend.app.db import init_db
from backend.app.federation import generate_test_keypair, membership_proposal, new_message_event, node_id_for_public_key, sign_membership_proposal, sign_event, verify_event
from fastapi import HTTPException

from backend.app.main import _accept_federation_event, federation_events, federation_missing, messages
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import OPERATOR, REPLIKA


class FederationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()
        self.keys = generate_test_keypair(b"host-a")
        self.other_keys = generate_test_keypair(b"host-b")
        with database.connect() as db:
            db.execute("INSERT INTO federation_hosts(host_id, domain, public_key, node_id, created_at) VALUES ('host-a', 'host-a.test', ?, ?, '2026-01-01T00:00:00Z')", (self.keys["public_key"], node_id_for_public_key(self.keys["public_key"])))
            db.execute("INSERT INTO federation_hosts(host_id, domain, public_key, node_id, created_at) VALUES ('host-b', 'host-b.test', ?, ?, '2026-01-01T00:00:00Z')", (self.other_keys["public_key"], node_id_for_public_key(self.other_keys["public_key"])))
            db.execute("INSERT INTO federation_rooms(room_id, host_id, quorum_m, quorum_n) VALUES ('default', 'host-a', 1, 1)")

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_signed_event_round_trip(self):
        event = new_message_event(room_id="default", author_id=OPERATOR, origin_host_id="host-a", body="Hello", mentions=[], references=[], private_key=self.keys["private_key"])
        self.assertTrue(verify_event(event, self.keys["public_key"]))
        event["payload"]["body"] = "tampered"
        self.assertFalse(verify_event(event, self.keys["public_key"]))

    def test_accepts_and_deduplicates_federated_message(self):
        event = new_message_event(room_id="default", author_id=OPERATOR, origin_host_id="host-a", body="Hello from host A", mentions=[REPLIKA], references=[], private_key=self.keys["private_key"])
        first = _accept_federation_event(event)
        second = _accept_federation_event(event)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages WHERE federation_event_id=?", (event["event_id"],)).fetchone()[0], 1)
            row = db.execute("SELECT source_type, provider, origin_host_id FROM messages WHERE id=?", (event["event_id"],)).fetchone()
        self.assertEqual(tuple(row), ("imported_transcript", "federation", "host-a"))

    def test_rejects_bad_signature_and_unknown_host(self):
        event = new_message_event(room_id="default", author_id=OPERATOR, origin_host_id="host-a", body="Hello", mentions=[], references=[], private_key=self.keys["private_key"])
        event["signature"] = "bad"
        with self.assertRaises(Exception):
            _accept_federation_event(event)
        event = new_message_event(room_id="default", author_id=OPERATOR, origin_host_id="unknown", body="Hello", mentions=[], references=[], private_key=self.keys["private_key"])
        with self.assertRaises(Exception):
            _accept_federation_event(event)

    def test_accepts_quorum_membership_change(self):
        proposal = membership_proposal(room_id="default", epoch=2, hosts=["host-a", "host-b"], quorum_m=2, quorum_n=2)
        payload = {"proposal": proposal, "quorum_signatures": [{"host_id": "host-a", "signature": sign_membership_proposal(proposal, self.keys["private_key"])}, {"host_id": "host-b", "signature": sign_membership_proposal(proposal, self.other_keys["private_key"])}]}
        event = sign_event({"protocol": "tinytable-federation-v0.1", "event_id": "membership-2", "room_id": "default", "event_type": "membership_change", "author_id": OPERATOR, "origin_host_id": "host-a", "created_at": "2026-01-01T00:00:01Z", "references": [], "payload": payload}, self.keys["private_key"])
        result = _accept_federation_event(event)
        self.assertTrue(result["accepted"])
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT membership_epoch FROM federation_rooms WHERE room_id='default' ORDER BY membership_epoch DESC LIMIT 1").fetchone()[0], 2)

    def test_conflicting_same_epoch_fails_closed(self):
        first = membership_proposal(room_id="default", epoch=2, hosts=["host-a", "host-b"], quorum_m=2, quorum_n=2)
        first_payload = {"proposal": first, "quorum_signatures": [{"host_id": host, "signature": sign_membership_proposal(first, self.keys["private_key"] if host == "host-a" else self.other_keys["private_key"])} for host in ("host-a", "host-b")]}
        _accept_federation_event(sign_event({"protocol": "tinytable-federation-v0.1", "event_id": "membership-first", "room_id": "default", "event_type": "membership_change", "author_id": OPERATOR, "origin_host_id": "host-a", "created_at": "2026-01-01T00:00:01Z", "references": [], "payload": first_payload}, self.keys["private_key"]))
        conflicting = membership_proposal(room_id="default", epoch=2, hosts=["host-a"], quorum_m=1, quorum_n=1)
        payload = {"proposal": conflicting, "quorum_signatures": [{"host_id": "host-a", "signature": sign_membership_proposal(conflicting, self.keys["private_key"])}]}
        event = sign_event({"protocol": "tinytable-federation-v0.1", "event_id": "membership-conflict", "room_id": "default", "event_type": "membership_change", "author_id": OPERATOR, "origin_host_id": "host-a", "created_at": "2026-01-01T00:00:02Z", "references": [], "payload": payload}, self.keys["private_key"])
        with self.assertRaises(Exception):
            _accept_federation_event(event)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT DISTINCT state FROM federation_rooms WHERE room_id='default'").fetchone()[0], "indeterminate")

    def _message(self, event_id, references, created_at="2026-01-01T00:00:00Z"):
        return new_message_event(room_id="default", author_id=OPERATOR, origin_host_id="host-a", body=event_id, mentions=[], references=references, private_key=self.keys["private_key"], event_id=event_id, created_at=created_at)

    def test_out_of_order_delivery_is_served_in_causal_order(self):
        root = self._message("root", [], "2026-01-01T00:00:10Z")
        reply = self._message("reply", ["root"], "2026-01-01T00:00:05Z")  # origin clock behind
        _accept_federation_event(reply)
        _accept_federation_event(root)
        self.assertEqual([event["event_id"] for event in federation_events(room_id="default")["events"]], ["root", "reply"])
        self.assertEqual([message["id"] for message in messages(room_id="default")], ["root", "reply"])

    def test_missing_references_are_recorded_and_cleared(self):
        _accept_federation_event(self._message("child", ["parent", "other"]))
        missing = {entry["event_id"]: entry["referenced_by"] for entry in federation_missing(room_id="default")["missing"]}
        self.assertEqual(missing, {"parent": ["child"], "other": ["child"]})
        _accept_federation_event(self._message("parent", []))
        self.assertEqual([entry["event_id"] for entry in federation_missing(room_id="default")["missing"]], ["other"])

    def test_rejects_malformed_references(self):
        for references in (["self"], ["a", "a"], [""], [1], ["r%d" % index for index in range(33)]):
            with self.assertRaises(HTTPException):
                _accept_federation_event(self._message("self", references))

    def test_reference_cycle_does_not_drop_events(self):
        _accept_federation_event(self._message("cycle-a", ["cycle-b"]))
        _accept_federation_event(self._message("cycle-b", ["cycle-a"]))
        _accept_federation_event(self._message("tail", []))
        self.assertEqual(sorted(event["event_id"] for event in federation_events(room_id="default")["events"]), ["cycle-a", "cycle-b", "tail"])


if __name__ == "__main__":
    unittest.main()
