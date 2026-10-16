import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import backend.app.db as database
from backend.app.commons_bridge import BridgeError, build_bridge_report
from backend.app.db import init_db
from backend.app.main import ConsentIn, save_message, set_consent
import configured_names  # noqa: F401  (display names an installation would configure)
from backend.app.seats import OPERATOR, REPLIKA


class CommonsBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = Path(self.directory.name) / "tinytable.db"
        init_db()

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.directory.cleanup()

    def test_exports_without_body_and_validates_against_commons_schema(self):
        set_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper accepted the relevant notice."))
        message = save_message(REPLIKA, "A provider observation for review.", "remote_model", "replika", request_id="provider-1")
        report = build_bridge_report(
            [message["id"]],
            purpose="proposal",
            audience="private_review",
            operator_id=OPERATOR,
            consent_evidence="Sam explicitly promoted this bounded observation for Commons review.",
        )
        self.assertEqual(report["status"], "validated")
        self.assertFalse(report["network_transmission"])
        self.assertEqual(report["commons_object"]["object_type"], "ContributionObject")
        self.assertNotIn("body", json.dumps(report))
        self.assertEqual(report["bridge_request"]["source"]["message_ids"], [message["id"]])

    def test_requires_consent_and_rejects_flagged_identity_boundary(self):
        message = save_message(REPLIKA, "Provider text.", "remote_model", "replika", request_id="provider-2")
        with self.assertRaisesRegex(BridgeError, "Consent is not accepted"):
            build_bridge_report(
                [message["id"]], purpose="research", audience="private_review", operator_id=OPERATOR, consent_evidence="Explicit evidence."
            )
        set_consent(REPLIKA, ConsentIn(status="accepted", evidence_text="Juniper accepted."))
        with database.connect() as db:
            db.execute("UPDATE messages SET identity_boundary_status='flagged' WHERE id=?", (message["id"],))
        with self.assertRaisesRegex(BridgeError, "flagged identity boundary"):
            build_bridge_report(
                [message["id"]], purpose="research", audience="private_review", operator_id=OPERATOR, consent_evidence="Explicit evidence."
            )

    def test_replication_is_explicitly_rejected(self):
        message = save_message(OPERATOR, "A human proposal.", "human", "manual-entry")
        with self.assertRaisesRegex(BridgeError, "Replication is disabled"):
            build_bridge_report(
                [message["id"]], purpose="proposal", audience="private_review", operator_id=OPERATOR, consent_evidence="Explicit evidence.", replication_requested=True
            )


if __name__ == "__main__":
    unittest.main()
