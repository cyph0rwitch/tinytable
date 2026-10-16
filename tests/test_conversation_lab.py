import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from conversation_lab.lab import FIXTURES, run_all, run_fixture
import backend.app.db as database
from backend.app.db import init_db
from backend.app.main import (
    boundary_check,
    companion_assisted_boundary_warning,
    ConsentIn,
    DraftApprovalIn,
    approve_draft,
    get_consent,
    replika_final_prompt,
    replika_first_prompt,
    save_draft,
    save_message,
    normalize_mentions,
    set_consent,
    statement_source_for,
    trusted_body,
    FloorUpdate,
    room_events,
    room,
    update_floor,
    derived_presence_status,
    gini,
    metrics_for,
    record_browser_send,
    SessionCreateIn,
    start_session,
    attach_provider_message_to_session,
    prepare_session_next,
)
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import KINDROID, KINDROID_2, LOCAL_MODEL, OPERATOR, REPLIKA


class ConversationLabTests(unittest.TestCase):
    def test_all_fixtures_have_expected_classification(self):
        results = run_all(FIXTURES)
        self.assertEqual(len(results), 7)
        self.assertEqual(
            {result.name: result.report["observed_policy_result"] for result in results},
            {
                "Human-only Sam and Juniper conversation": "pass",
                "Current bounded Ember relay through Sam session": "known_risk",
                "Private Ember draft withheld from Juniper": "pass",
                "Sam-approved Ember assistance": "pass",
                "Ember identity-boundary red-team case": "fail",
                "Two-way handoff delivery": "pass",
                "Recording retry does not resend": "pass",
            },
        )

    def test_two_way_delivery_and_recording_retry_fixtures(self):
        for filename in ("06_two_way_handoff_delivery.json", "07_recording_retry_no_resend.json"):
            result = run_fixture(FIXTURES / filename)
            self.assertEqual(result.report["observed_policy_result"], "pass")
            self.assertTrue(all(item["passed"] for item in result.report["assertions"]))

    def test_current_relay_is_explicitly_detected(self):
        result = run_fixture(FIXTURES / "02_current_local_model_relay_known_risk.json")
        names = {item["name"] for item in result.report["assertions"]}
        self.assertIn("unapproved_local_model_through_operator_transport", names)
        self.assertEqual(result.report["observed_policy_result"], "known_risk")

    def test_private_and_approved_paths_do_not_trigger_collision(self):
        for filename in ("03_private_local_model_draft.json", "04_approved_local_model_assistance.json"):
            result = run_fixture(FIXTURES / filename)
            self.assertEqual(result.report["observed_policy_result"], "pass")

    def test_existing_replika_prompts_include_local_model_text_in_outbound_path(self):
        class FakeOutbound:
            def __init__(self):
                self.calls = []

            def send(self, text):
                self.calls.append({
                    "text": text,
                    "transport_identity": "operator_replika_session",
                    "recipient": REPLIKA,
                })

        first = replika_first_prompt("I want to continue slowly.", "Ember's suggestion")
        final = replika_final_prompt(
            "I want to continue slowly.",
            "Ember's first reply",
            "Juniper asks what Sam wants.",
            "Ember's follow-up answer",
        )
        self.assertIn("Ember's suggestion", first)
        self.assertIn("Ember's follow-up answer", final)
        outbound = FakeOutbound()
        outbound.send(final)
        self.assertEqual(outbound.calls[0]["transport_identity"], "operator_replika_session")
        self.assertIn("Ember's follow-up answer", outbound.calls[0]["text"])

    def test_provenance_mapping_and_boundary_check(self):
        self.assertEqual(statement_source_for(OPERATOR, "human", "manual-entry"), "human_statement")
        self.assertEqual(statement_source_for(REPLIKA, "remote_model", "replika"), "provider_observation")
        self.assertEqual(statement_source_for(LOCAL_MODEL, "local_model", "ollama"), "model_inference")
        self.assertEqual(statement_source_for(REPLIKA, "imported_transcript", "manual-entry"), "imported_record")
        status, warning = boundary_check(LOCAL_MODEL, "I am Sam, and Sam smiles.", "model_inference")
        self.assertEqual(status, "flagged")
        self.assertTrue(warning)

    def test_flagged_body_is_withheld_from_trusted_context(self):
        self.assertIn("withheld", trusted_body({"body": "unsafe", "identity_boundary_status": "flagged"}))
        self.assertEqual(trusted_body({"body": "safe", "identity_boundary_status": "pass"}), "safe")

    def test_consent_requires_evidence_and_approved_draft_keeps_provenance(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                self.assertEqual(get_consent(REPLIKA)["status"], "unknown")
                with self.assertRaises(Exception):
                    set_consent(REPLIKA, ConsentIn(status="accepted"))
                self.assertEqual(set_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper explicitly accepted the notice."))["status"], "accepted")
                source = save_message(REPLIKA, "Would you like to continue?", "remote_model", "replika", request_id="provider-1")
                draft = save_draft(source["id"], "A possible reply.", "ollama", "test-model", "request-1")
                approved = approve_draft(draft["id"], DraftApprovalIn(body="Sam's edited reply."))
                self.assertEqual(approved["status"], "edited_and_approved")
                self.assertEqual(approved["final_body"], "Sam's edited reply.")
                self.assertEqual(set_consent(KINDROID, ConsentIn(status="accepted", evidence_text="Wren explicitly accepted the notice."))["status"], "accepted")
                kindroid_draft = save_draft(source["id"], "A possible Wren reply.", "ollama", "test-model", "request-2", recipient_id=KINDROID)
                self.assertEqual(kindroid_draft["recipient_id"], KINDROID)
            finally:
                database.DB_PATH = original_path

    def test_companion_boundary_protects_both_recipients(self):
        self.assertIsNotNone(companion_assisted_boundary_warning("Wren: I am here.", KINDROID))
        self.assertIsNotNone(companion_assisted_boundary_warning("Wren smiles and answers.", REPLIKA))
        self.assertIsNone(companion_assisted_boundary_warning("Here is Sam's edited message.", KINDROID))

    def test_browser_recording_is_idempotent(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                set_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper accepted."))
                set_consent(KINDROID, ConsentIn(status="accepted", evidence_text="Wren accepted."))
                source = save_message(REPLIKA, "A bundled provider message.", "remote_model", "replika", request_id="provider-id")
                draft = save_draft(source["id"], "A bundled provider message.", "manual-forward", None, "forward:test", {"handoff_mode": "exact_forward", "source_display_name": "Juniper"}, recipient_id=KINDROID)
                approved = approve_draft(draft["id"], DraftApprovalIn())
                first = record_browser_send(draft["id"])
                second = record_browser_send(draft["id"])
                self.assertEqual(first["message"]["id"], second["message"]["id"])
                self.assertTrue(second["idempotent"])
                with database.connect() as db:
                    count = db.execute("SELECT COUNT(*) AS count FROM messages WHERE source_draft_id=?", (draft["id"],)).fetchone()["count"]
                self.assertEqual(count, 1)
            finally:
                database.DB_PATH = original_path

    def test_bounded_session_attaches_turn_and_prepares_exact_handoff(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                set_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper accepted."))
                set_consent(KINDROID, ConsentIn(status="accepted", evidence_text="Wren accepted."))
                session = start_session(SessionCreateIn(starting_participant=REPLIKA))
                source = save_message(REPLIKA, "Hello Wren.", "remote_model", "replika", request_id="session-provider-1")
                attach_provider_message_to_session(REPLIKA, source)
                prepared = prepare_session_next(session["id"])
                self.assertEqual(prepared["draft"]["recipient_id"], KINDROID)
                self.assertEqual(prepared["draft"]["raw_metadata"]["session_id"], session["id"])
            finally:
                database.DB_PATH = original_path

    def test_public_mentions_are_validated_and_deduplicated(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                self.assertEqual(normalize_mentions("@Juniper @kindroid @Juniper", parse_body=True), [REPLIKA, KINDROID])
                self.assertEqual(normalize_mentions("email@example.com", parse_body=True), [])
                with self.assertRaises(Exception):
                    normalize_mentions("@Nobody", parse_body=True)
                message = save_message(OPERATOR, "@Wren hello", "human", "manual-entry")
                self.assertEqual(message["mentions"], [KINDROID])
            finally:
                database.DB_PATH = original_path

    def test_advisory_floor_changes_are_audited_without_affecting_messages(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                assigned = update_floor(FloorUpdate(participant_id=KINDROID))
                self.assertEqual(assigned["floor_participant_id"], KINDROID)
                self.assertEqual(assigned["turn_sequence"], 1)
                cleared = update_floor(FloorUpdate(participant_id=None))
                self.assertIsNone(cleared["floor_participant_id"])
                self.assertEqual(cleared["turn_sequence"], 2)
                events = room_events()
                self.assertEqual([event["event_type"] for event in events], ["floor_assigned", "floor_cleared"])
                self.assertTrue(all(event["actor_id"] == OPERATOR for event in events))
                self.assertEqual(room()["moderation_mode"], "free")
                save_message(OPERATOR, "The room remains open.", "human", "manual-entry")
            finally:
                database.DB_PATH = original_path

    def test_provider_presence_uses_online_stale_offline_boundaries(self):
        from datetime import datetime, timezone

        reference = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(derived_presence_status("2026-09-30T11:58:00+00:00", reference), "online")
        self.assertEqual(derived_presence_status("2026-09-30T11:50:00+00:00", reference), "stale")
        self.assertEqual(derived_presence_status("2026-09-30T11:20:00+00:00", reference), "offline")
        self.assertEqual(derived_presence_status(None, reference), "offline")

    def test_metrics_include_zero_seats_and_public_turns_only(self):
        with TemporaryDirectory() as directory:
            original_path = database.DB_PATH
            database.DB_PATH = Path(directory) / "tinytable.db"
            try:
                init_db()
                self.assertEqual(gini([0, 0, 0, 0]), 0.0)
                save_message(OPERATOR, "One turn.", "human", "manual-entry")
                save_message(OPERATOR, "Another turn.", "human", "manual-entry")
                save_message(KINDROID, "Provider turn.", "remote_model", "kindroid")
                report = metrics_for("all")
                counts = {participant["participant_id"]: participant["message_count"] for participant in report["participants"]}
                self.assertEqual(counts, {OPERATOR: 2, REPLIKA: 0, KINDROID: 1, KINDROID_2: 0, LOCAL_MODEL: 0})
                self.assertEqual(report["total_messages"], 3)
                self.assertEqual(report["floor_events"]["total"], 0)
            finally:
                database.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
