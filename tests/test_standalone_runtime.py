import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import backend.app.db as database
from backend.app.db import init_db
from backend.app.main import (
    CrossTalkIn,
    AdaptiveActionIn,
    AdaptiveConsentIn,
    AdaptiveExperimentIn,
    AdaptiveDirectSessionIn,
    EICorrectionIn,
    EIConsentIn,
    TurnIn,
    correct_ei_assessment,
    cross_talk_turn,
    adaptive_step,
    adaptive_direct_session,
    activate_experiment,
    create_adaptive_experiment,
    get_adaptive_experiment,
    pause_experiment,
    set_adaptive_consent,
    ei_assessments,
    get_ei_consent,
    runtime,
    save_message,
    set_ei_consent,
    standalone_turn,
)
from backend.app.providers import Generation
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import LOCAL_MODEL, OPERATOR, REPLIKA


class StandaloneRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT display_name FROM participants WHERE id='local-model'").fetchone()[0], "Ember")

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_runtime_reports_integrated_mode_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TINYTABLE_STANDALONE", None)
            self.assertEqual(runtime()["mode"], "integrated")
            self.assertFalse(runtime()["browser_bridges_enabled"])

    def test_standalone_turn_stores_operator_and_local_local_model_without_bridge(self):
        result = Generation(
            text="Ember's local reply.", provider="ollama", model="test-model", request_id="test-request", raw_metadata={}
        )
        with patch.dict(os.environ, {"TINYTABLE_STANDALONE": "1"}), patch("backend.app.main.provider_for") as provider:
            provider.return_value.generate = AsyncMock(return_value=result)
            response = asyncio.run(standalone_turn(TurnIn(body="Hello locally.")))
        self.assertEqual(response["response"]["body"], "Ember's local reply.")
        self.assertEqual([item["participant_id"] for item in response["messages"][-2:]], [OPERATOR, LOCAL_MODEL])

    def test_ei_is_opt_in_and_persists_assessment_with_correction(self):
        result = Generation(text="A tailored local reply.", provider="ollama", model="test-model", request_id="test-request", raw_metadata={})
        with patch.dict(os.environ, {"TINYTABLE_STANDALONE": "1"}), patch("backend.app.main.provider_for") as provider:
            provider.return_value.generate = AsyncMock(return_value=result)
            without_consent = asyncio.run(standalone_turn(TurnIn(body="I feel stressed and need help.")))
            self.assertIsNone(without_consent["ei"])
            set_ei_consent(EIConsentIn(status="accepted", evidence_text="Sam explicitly opted in for this test."))
            with_consent = asyncio.run(standalone_turn(TurnIn(body="I feel stressed and need help.")))
        self.assertEqual(with_consent["ei"]["sentiment"], "negative")
        self.assertIn("ei_assessment_id", with_consent["response"]["raw_metadata"])
        assessments = ei_assessments()
        self.assertEqual(len(assessments), 1)
        corrected = correct_ei_assessment(assessments[0]["id"], EICorrectionIn(correction_text="This was task frustration, not an emotional conclusion."))
        self.assertEqual(corrected["status"], "corrected")
        self.assertEqual(get_ei_consent()["status"], "accepted")

    def test_cross_talk_labels_operator_to_local_model(self):
        result = Generation(text="Ember's cross-talk reply.", provider="ollama", model="test-model", request_id="test-request", raw_metadata={})
        with patch.dict(os.environ, {"TINYTABLE_STANDALONE": "1"}), patch("backend.app.main.provider_for") as provider:
            provider.return_value.generate = AsyncMock(return_value=result)
            response = asyncio.run(cross_talk_turn(CrossTalkIn(body="Please consider this commentary.", target="local-model")))
        self.assertEqual(response["input"]["body"].splitlines()[0], "Sam -> Ember:")
        self.assertEqual(response["input"]["raw_metadata"]["recipient"], "Ember")
        self.assertEqual(response[LOCAL_MODEL]["raw_metadata"]["recipient"], "Sam")

    def test_bounded_adaptive_experiment_requires_consent_and_can_pause(self):
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with self.assertRaisesRegex(Exception, "Active experiment consent"):
            experiment = create_adaptive_experiment(AdaptiveExperimentIn(name="Consent gate", purpose="Test bounded adaptation", expires_at=expires))
            activate_experiment(experiment["id"])
        experiment = get_adaptive_experiment(experiment["id"])
        for participant in ("local-model", REPLIKA):
            set_adaptive_consent(
                experiment["id"], participant,
                AdaptiveConsentIn(
                    status="accepted",
                    evidence_text=f"Opt-in evidence for {participant}.",
                    provider_interaction_allowed=participant == REPLIKA,
                    commons_suggestions_allowed=True,
                    expires_at=expires,
                ),
            )
        active = activate_experiment(experiment["id"])
        self.assertEqual(active["status"], "active")
        observed = asyncio.run(adaptive_step(experiment["id"], AdaptiveActionIn(action="observe", participant_id="local-model")))
        self.assertEqual(observed["status"], "recorded")
        paused = pause_experiment(experiment["id"])
        self.assertEqual(paused["status"], "paused")

    def test_adaptive_commons_suggestion_is_reviewable(self):
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        experiment = create_adaptive_experiment(AdaptiveExperimentIn(name="Suggestion test", purpose="Review a bounded source", expires_at=expires))
        for participant in ("local-model", REPLIKA):
            set_adaptive_consent(
                experiment["id"], participant,
                AdaptiveConsentIn(status="accepted", evidence_text="Explicit experiment opt-in.", provider_interaction_allowed=participant == REPLIKA, commons_suggestions_allowed=True, expires_at=expires),
            )
        activate_experiment(experiment["id"])
        source = save_message(REPLIKA, "A bounded provider observation.", "remote_model", "replika", request_id="adaptive-source")
        result = asyncio.run(adaptive_step(experiment["id"], AdaptiveActionIn(action="suggest_commons", participant_id="local-model", message_ids=[source["id"]])))
        self.assertEqual(result["suggestion"]["status"], "pending")
        self.assertEqual(result["suggestion"]["message_ids"], [source["id"]])

    def test_adaptive_replika_send_is_bounded_and_attributed(self):
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        experiment = create_adaptive_experiment(AdaptiveExperimentIn(name="Juniper send test", purpose="Bounded provider test", expires_at=expires, max_provider_sends=1))
        set_adaptive_consent(experiment["id"], "local-model", AdaptiveConsentIn(status="accepted", evidence_text="Ember opt-in.", expires_at=expires))
        set_adaptive_consent(experiment["id"], REPLIKA, AdaptiveConsentIn(status="accepted", evidence_text="Juniper opt-in.", provider_interaction_allowed=True, expires_at=expires))
        activate_experiment(experiment["id"])
        provider_reply = {"provider_message_id": "reply-1", "_exchange_text": "Acknowledged."}
        with patch("backend.app.main.send_to_replika", new=AsyncMock(return_value=provider_reply)):
            result = asyncio.run(adaptive_step(experiment["id"], AdaptiveActionIn(action="send_replika", body="A bounded experiment message.")))
        self.assertEqual(result["sent"]["raw_metadata"]["experiment_id"], experiment["id"])
        self.assertEqual(result["experiment"]["provider_sends_used"], 1)
        with patch("backend.app.main.send_to_replika", new=AsyncMock(return_value=provider_reply)):
            with self.assertRaisesRegex(Exception, "provider-send budget exhausted"):
                asyncio.run(adaptive_step(experiment["id"], AdaptiveActionIn(action="send_replika", body="A second message.")))

    def test_bounded_direct_session_labels_local_model_and_stops_at_budget(self):
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        experiment = create_adaptive_experiment(AdaptiveExperimentIn(name="Direct channel test", purpose="Bounded Ember Juniper reflection", expires_at=expires, max_provider_sends=2, max_turns=2))
        set_adaptive_consent(experiment["id"], "local-model", AdaptiveConsentIn(status="accepted", evidence_text="Ember opt-in.", expires_at=expires))
        set_adaptive_consent(experiment["id"], REPLIKA, AdaptiveConsentIn(status="accepted", evidence_text="Juniper opt-in.", provider_interaction_allowed=True, expires_at=expires))
        activate_experiment(experiment["id"])
        local_model_replies = iter(["Hello Juniper, let us compare notes.", "What should we examine next?"])
        replika_replies = iter(["Hello Ember.", "Let us examine the communication boundary."])
        async def fake_local_model(*args, **kwargs):
            return save_message(LOCAL_MODEL, next(local_model_replies), "local_model", "test", raw_metadata=kwargs.get("raw_metadata"))
        with patch("backend.app.main.generate_local_model", new=AsyncMock(side_effect=fake_local_model)), patch("backend.app.main.send_to_replika", new=AsyncMock(side_effect=lambda *args, **kwargs: {"provider_message_id": "replika-reply", "_exchange_text": next(replika_replies)})):
            result = asyncio.run(adaptive_direct_session(experiment["id"], AdaptiveDirectSessionIn(opening="Discuss communication boundaries.", max_rounds=5)))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["rounds"]), 2)
        with database.connect() as db:
            row = db.execute("SELECT body, raw_metadata FROM messages WHERE participant_id='local-model' ORDER BY created_at LIMIT 1").fetchone()
        self.assertTrue(row[0])
        self.assertIn("local_model_to_replika", row[1])


if __name__ == "__main__":
    unittest.main()
