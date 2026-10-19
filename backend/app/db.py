import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import seats
from .config import default_db_path
from .federation import node_id_for_public_key
from .seats import COMPANIONS, CHANNEL_ROOM_ID, KINDROID, KINDROID_2, KINDROID_2_CHANNEL, KINDROID_CHANNEL, KINDROID_GROUP_CHANNEL, LOCAL_MODEL, OPERATOR, PROVIDER_SEATS, REPLIKA

DB_PATH = Path(os.getenv("TINYTABLE_DB") or default_db_path())


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    # Containers run with a read-only root filesystem, so SQLite cannot create
    # temporary files in /tmp; keep them in memory.
    db.execute("PRAGMA temp_store = MEMORY")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS participants (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            pronouns TEXT NOT NULL DEFAULT '',
            avatar TEXT NOT NULL DEFAULT '◉',
            description TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL CHECK(kind IN ('human','agent')),
            provider_type TEXT NOT NULL,
            provider_config TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL CHECK(status IN ('online','offline','manual')) DEFAULT 'offline',
            last_seen_at TEXT,
            last_seen_source TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS room_state (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            name TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            moderation_mode TEXT NOT NULL CHECK(moderation_mode IN ('free', 'moderated')) DEFAULT 'free',
            floor_participant_id TEXT REFERENCES participants(id),
            turn_sequence INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            topic TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS room_members (
            room_id TEXT NOT NULL REFERENCES rooms(id),
            participant_id TEXT NOT NULL REFERENCES participants(id),
            role TEXT NOT NULL DEFAULT 'member',
            enabled INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (room_id, participant_id)
        );
        CREATE TABLE IF NOT EXISTS room_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL CHECK(event_type IN ('floor_assigned', 'floor_cleared')),
            participant_id TEXT REFERENCES participants(id),
            actor_id TEXT NOT NULL REFERENCES participants(id),
            turn_sequence INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_room_events_created_at ON room_events(created_at);
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            participant_id TEXT NOT NULL REFERENCES participants(id),
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            request_id TEXT,
            source_type TEXT NOT NULL CHECK(source_type IN ('human','local_model','remote_model','imported_transcript','manual')),
            raw_metadata TEXT NOT NULL DEFAULT '{}',
            mentions TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
        CREATE TABLE IF NOT EXISTS participant_consents (
            participant_id TEXT PRIMARY KEY REFERENCES participants(id),
            status TEXT NOT NULL CHECK(status IN ('unknown', 'accepted', 'declined')) DEFAULT 'unknown',
            assistance_allowed INTEGER NOT NULL DEFAULT 1,
            notice_version TEXT NOT NULL DEFAULT 'v1',
            evidence_text TEXT NOT NULL DEFAULT '',
            recorded_by TEXT NOT NULL DEFAULT 'operator',
            recorded_at TEXT,
            source TEXT NOT NULL DEFAULT 'operator_recorded'
        );
        CREATE TABLE IF NOT EXISTS ei_consent (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            status TEXT NOT NULL CHECK(status IN ('unknown', 'accepted', 'declined')) DEFAULT 'unknown',
            notice_version TEXT NOT NULL DEFAULT 'v1',
            evidence_text TEXT NOT NULL DEFAULT '',
            recorded_by TEXT NOT NULL DEFAULT 'operator',
            recorded_at TEXT,
            granted_at TEXT,
            revoked_at TEXT,
            source TEXT NOT NULL DEFAULT 'local_operator'
        );
        CREATE TABLE IF NOT EXISTS ei_assessments (
            id TEXT PRIMARY KEY,
            message_id TEXT REFERENCES messages(id),
            assessment_type TEXT NOT NULL CHECK(assessment_type IN ('contextualization', 'sentiment', 'mentorship')),
            sentiment TEXT NOT NULL,
            context_label TEXT NOT NULL,
            confidence REAL NOT NULL,
            uncertainty TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            recommendation_mode TEXT NOT NULL,
            correction_text TEXT,
            excluded_from_reply INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL CHECK(status IN ('active', 'corrected', 'deleted', 'expired')) DEFAULT 'active',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            corrected_at TEXT,
            deleted_at TEXT,
            raw_metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_ei_assessments_created ON ei_assessments(created_at);
        CREATE INDEX IF NOT EXISTS idx_ei_assessments_expiry ON ei_assessments(expires_at, status);
        CREATE TABLE IF NOT EXISTS drafts (
            id TEXT PRIMARY KEY,
            source_message_id TEXT REFERENCES messages(id),
            recipient_id TEXT NOT NULL REFERENCES participants(id),
            body TEXT NOT NULL,
            final_body TEXT,
            created_at TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT,
            request_id TEXT,
            status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'edited_and_approved', 'declined', 'sent')) DEFAULT 'pending',
            approved_at TEXT,
            sent_at TEXT,
            archived_at TEXT,
            delivery_state TEXT NOT NULL DEFAULT 'received',
            recording_error TEXT,
            raw_metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status, created_at);
        CREATE TABLE IF NOT EXISTS provider_channels (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel_type TEXT NOT NULL CHECK(channel_type IN ('private', 'group')),
            url TEXT NOT NULL,
            participant_ids TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS provider_identity_bindings (
            provider TEXT NOT NULL,
            participant_id TEXT NOT NULL REFERENCES participants(id),
            channel_id TEXT NOT NULL REFERENCES provider_channels(id),
            account_hash TEXT NOT NULL,
            channel_hash TEXT NOT NULL,
            speaker_hash TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(provider, participant_id, channel_id)
        );
        CREATE INDEX IF NOT EXISTS idx_provider_identity_channel ON provider_identity_bindings(channel_id, enabled);
        CREATE TABLE IF NOT EXISTS conversation_sessions (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK(status IN ('active','completed','stopped','recording_failed')),
            starting_participant TEXT NOT NULL REFERENCES participants(id),
            expected_participant TEXT NOT NULL REFERENCES participants(id),
            turn_count INTEGER NOT NULL DEFAULT 0,
            max_turns INTEGER NOT NULL DEFAULT 10,
            stop_reason TEXT,
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            stopped_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cli_agents (
            participant_id TEXT PRIMARY KEY REFERENCES participants(id),
            transport TEXT NOT NULL CHECK(transport IN ('hermes_local', 'hermes_peer')),
            target TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delivery_attempts (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES rooms(id),
            message_id TEXT NOT NULL REFERENCES messages(id),
            target_participant_id TEXT NOT NULL REFERENCES participants(id),
            transport TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending', 'delivered', 'failed', 'timeout')),
            external_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_delivery_attempts_message ON delivery_attempts(message_id, created_at);
        CREATE TABLE IF NOT EXISTS session_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES conversation_sessions(id),
            turn_number INTEGER NOT NULL,
            participant_id TEXT NOT NULL REFERENCES participants(id),
            message_ids TEXT NOT NULL DEFAULT '[]',
            body TEXT NOT NULL,
            recipient_id TEXT REFERENCES participants(id),
            draft_id TEXT REFERENCES drafts(id),
            delivery_state TEXT NOT NULL DEFAULT 'received',
            created_at TEXT NOT NULL,
            UNIQUE(session_id, turn_number)
        );
        CREATE INDEX IF NOT EXISTS idx_session_turns_session ON session_turns(session_id, turn_number);
        CREATE TABLE IF NOT EXISTS channel_audit (
            id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES rooms(id),
            session_id TEXT REFERENCES conversation_sessions(id),
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_channel_audit_room ON channel_audit(room_id, created_at);
        CREATE TABLE IF NOT EXISTS adaptive_experiments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            purpose TEXT NOT NULL,
            strategy_version TEXT NOT NULL DEFAULT 'deterministic-v1',
            participant_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL CHECK(status IN ('draft','active','paused','stopped','completed','rolled_back')) DEFAULT 'draft',
            starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            max_provider_sends INTEGER NOT NULL,
            max_turns INTEGER NOT NULL,
            provider_sends_used INTEGER NOT NULL DEFAULT 0,
            turns_used INTEGER NOT NULL DEFAULT 0,
            consent_snapshot TEXT NOT NULL DEFAULT '{}',
            created_by TEXT NOT NULL DEFAULT 'operator',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            stopped_at TEXT,
            stop_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_adaptive_experiments_status ON adaptive_experiments(status, expires_at);
        CREATE TABLE IF NOT EXISTS adaptive_consents (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES adaptive_experiments(id),
            participant_id TEXT NOT NULL REFERENCES participants(id),
            status TEXT NOT NULL CHECK(status IN ('accepted','declined','revoked')),
            purpose TEXT NOT NULL,
            provider_interaction_allowed INTEGER NOT NULL DEFAULT 0,
            commons_suggestions_allowed INTEGER NOT NULL DEFAULT 0,
            evidence_text TEXT NOT NULL,
            notice_version TEXT NOT NULL DEFAULT 'v1',
            granted_at TEXT,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            recorded_by TEXT NOT NULL DEFAULT 'operator',
            UNIQUE(experiment_id, participant_id)
        );
        CREATE TABLE IF NOT EXISTS adaptive_events (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES adaptive_experiments(id),
            event_type TEXT NOT NULL,
            action TEXT,
            participant_id TEXT,
            message_ids TEXT NOT NULL DEFAULT '[]',
            policy_decision TEXT NOT NULL DEFAULT '{}',
            outcome TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_adaptive_events_experiment ON adaptive_events(experiment_id, created_at);
        CREATE TABLE IF NOT EXISTS adaptive_suggestions (
            id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL REFERENCES adaptive_experiments(id),
            message_ids TEXT NOT NULL,
            purpose TEXT NOT NULL,
            audience TEXT NOT NULL,
            retention_expires_at TEXT NOT NULL,
            rationale TEXT NOT NULL,
            content_commitment TEXT NOT NULL,
            selection_commitment TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','reviewed','declined','invalidated','promoted')) DEFAULT 'pending',
            bridge_report TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_adaptive_suggestions_status ON adaptive_suggestions(experiment_id, status, created_at);
        CREATE TABLE IF NOT EXISTS federation_hosts (
            host_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            public_key TEXT NOT NULL,
            node_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','revoked','unknown')) DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS federation_rooms (
            room_id TEXT NOT NULL REFERENCES rooms(id),
            host_id TEXT NOT NULL REFERENCES federation_hosts(host_id),
            quorum_m INTEGER NOT NULL CHECK(quorum_m >= 1),
            quorum_n INTEGER NOT NULL CHECK(quorum_n >= quorum_m),
            membership_epoch INTEGER NOT NULL DEFAULT 1,
            state TEXT NOT NULL CHECK(state IN ('active','indeterminate')) DEFAULT 'active',
            PRIMARY KEY (room_id, host_id)
        );
        CREATE TABLE IF NOT EXISTS federation_events (
            event_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL REFERENCES rooms(id),
            event_type TEXT NOT NULL,
            origin_host_id TEXT NOT NULL,
            event_hash TEXT NOT NULL,
            event_json TEXT NOT NULL,
            received_at TEXT NOT NULL,
            UNIQUE(room_id, event_hash)
        );
        CREATE TABLE IF NOT EXISTS federation_missing_references (
            room_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            missing_event_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY(event_id, missing_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_federation_missing_references_missing ON federation_missing_references(missing_event_id);
        CREATE TABLE IF NOT EXISTS federation_peer_cursors (
            room_id TEXT NOT NULL,
            peer_host_id TEXT NOT NULL,
            last_event_id TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(room_id, peer_host_id)
        );
        """)
        host_columns = {row[1] for row in db.execute("PRAGMA table_info(federation_hosts)")}
        if "node_id" not in host_columns:
            db.execute("ALTER TABLE federation_hosts ADD COLUMN node_id TEXT")
        for host in db.execute("SELECT host_id, public_key, node_id FROM federation_hosts").fetchall():
            try:
                computed_node_id = node_id_for_public_key(host["public_key"])
            except (TypeError, ValueError) as exc:
                raise RuntimeError(f"Invalid public key for federation host {host['host_id']}") from exc
            if host["node_id"] and host["node_id"] != computed_node_id:
                raise RuntimeError(f"Stored node ID does not match public key for federation host {host['host_id']}")
            db.execute("UPDATE federation_hosts SET node_id=? WHERE host_id=?", (computed_node_id, host["host_id"]))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_federation_hosts_node_id ON federation_hosts(node_id)")
    migrate_legacy_ids()
    with connect() as db:
        seed(db)
        migrate_messages(db)
        migrate_room_columns(db)
        participant_columns = {row[1] for row in db.execute("PRAGMA table_info(participants)")}
        if "last_seen_at" not in participant_columns:
            db.execute("ALTER TABLE participants ADD COLUMN last_seen_at TEXT")
        if "last_seen_source" not in participant_columns:
            db.execute("ALTER TABLE participants ADD COLUMN last_seen_source TEXT")
        consent_columns = {row[1] for row in db.execute("PRAGMA table_info(participant_consents)")}
        if "assistance_allowed" not in consent_columns:
            db.execute("ALTER TABLE participant_consents ADD COLUMN assistance_allowed INTEGER NOT NULL DEFAULT 1")
        draft_columns = {row[1] for row in db.execute("PRAGMA table_info(drafts)")}
        if "archived_at" not in draft_columns:
            db.execute("ALTER TABLE drafts ADD COLUMN archived_at TEXT")
        if "delivery_state" not in draft_columns:
            db.execute("ALTER TABLE drafts ADD COLUMN delivery_state TEXT NOT NULL DEFAULT 'received'")
        if "recording_error" not in draft_columns:
            db.execute("ALTER TABLE drafts ADD COLUMN recording_error TEXT")
        room_timestamp = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT OR IGNORE INTO room_state (id, name, topic, moderation_mode, floor_participant_id, turn_sequence, updated_at) VALUES (1, ?, ?, 'free', NULL, 0, ?)",
            ("#tinytable", "An open room for the operator, the companions, the local model, and the wider Commons.", room_timestamp),
        )
        db.execute(
            "INSERT OR IGNORE INTO rooms (id, name, topic, created_at) VALUES ('default', '#tinytable', 'Companion conversation room.', ?)",
            (room_timestamp,),
        )
        db.execute(
            "INSERT OR IGNORE INTO rooms (id, name, topic, created_at) VALUES ('cli:hermes-estate', 'CLI agents', 'Directed conversation with the configured Hermes CLI agents.', ?)",
            (room_timestamp,),
        )
        db.executemany(
            "INSERT OR IGNORE INTO room_members (room_id, participant_id, role, enabled) VALUES ('default', ?, 'member', 1)",
            [(participant_id,) for participant_id in (OPERATOR, REPLIKA, KINDROID, KINDROID_2, LOCAL_MODEL)],
        )
        # The Replika seat's availability is controlled by the receive/send
        # bridge rather than the manual-seat placeholder.
        db.execute("UPDATE participants SET status='online' WHERE id=?", (REPLIKA,))
        # Chat URLs identify the provider-side AI, so they come from
        # configuration rather than the repository.
        channels = [
            (KINDROID_CHANNEL, f"{seats.name(KINDROID)} private", "private", os.getenv("TINYTABLE_KINDROID_COMPANION_URL", "https://kindroid.ai/"), [KINDROID]),
            (KINDROID_2_CHANNEL, f"{seats.name(KINDROID_2)} private", "private", os.getenv("TINYTABLE_KINDROID_COMPANION_2_URL", "https://kindroid.ai/"), [KINDROID_2]),
            (KINDROID_GROUP_CHANNEL, f"{seats.name(KINDROID_2)} + {seats.name(KINDROID)} group", "group", os.getenv("TINYTABLE_KINDROID_GROUP_URL", "https://kindroid.ai/"), [KINDROID_2, KINDROID]),
        ]
        for channel_id, display_name, channel_type, url, members in channels:
            db.execute(
                "INSERT INTO provider_channels (id, provider, display_name, channel_type, url, participant_ids) VALUES (?, 'kindroid', ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name, url=excluded.url",
                (channel_id, display_name, channel_type, url, json.dumps(members)),
            )
        # Keep the existing local-model row aligned with the current local
        # configuration. INSERT OR IGNORE alone would leave an old provider
        # URL or model in an already-created database.
        local_model_config = {
            "base_url": os.getenv("TINYTABLE_PROVIDER_BASE_URL", "http://localhost:11434/v1"),
            "model": os.getenv("TINYTABLE_PROVIDER_MODEL", "llama3.2"),
        }
        db.execute(
            "UPDATE participants SET provider_type='openai_compatible', provider_config=?, status='online' WHERE id=?",
            (json.dumps(local_model_config), LOCAL_MODEL),
        )
        # Display identities follow configuration on every start.
        for seat in (OPERATOR, REPLIKA, KINDROID, KINDROID_2, LOCAL_MODEL):
            db.execute(
                "UPDATE participants SET display_name=?, pronouns=?, avatar=? WHERE id=?",
                (seats.name(seat), seats.pronouns(seat), seats.avatar(seat), seat),
            )
        for participant_id, display_name, avatar, description, transport, config in configured_cli_agents():
            db.execute(
                "INSERT OR IGNORE INTO participants (id, display_name, pronouns, avatar, description, kind, provider_type, provider_config, status, created_at) VALUES (?, ?, '', ?, ?, 'agent', ?, ?, 'offline', ?)",
                (participant_id, display_name, avatar, description, transport, json.dumps(config), room_timestamp),
            )
            db.execute(
                "INSERT OR IGNORE INTO cli_agents (participant_id, transport, target, config, created_at) VALUES (?, ?, ?, ?, ?)",
                (participant_id, transport, config.get("profile") or config.get("peer") or participant_id, json.dumps(config), room_timestamp),
            )
            db.execute(
                "INSERT OR IGNORE INTO room_members (room_id, participant_id, role, enabled) VALUES ('cli:hermes-estate', ?, 'agent', 1)",
                (participant_id,),
            )
        db.execute(
            "INSERT OR IGNORE INTO room_members (room_id, participant_id, role, enabled) VALUES ('cli:hermes-estate', ?, 'operator', 1)",
            (OPERATOR,),
        )
        # The two companions talk only through this bounded channel; membership
        # is explicit so a later federated room can carry the same roster.
        db.execute(
            "INSERT OR IGNORE INTO rooms (id, name, topic, created_at) VALUES (?, 'Companion table', 'Bounded exact-forward channel between the two provider companions; every hop is operator-approved.', ?)",
            (CHANNEL_ROOM_ID, room_timestamp),
        )
        db.executemany(
            "INSERT OR IGNORE INTO room_members (room_id, participant_id, role, enabled) VALUES (?, ?, ?, 1)",
            [(CHANNEL_ROOM_ID, OPERATOR, "operator"), (CHANNEL_ROOM_ID, REPLIKA, "member"), (CHANNEL_ROOM_ID, KINDROID, "member")],
        )
        session_columns = {row[1] for row in db.execute("PRAGMA table_info(conversation_sessions)")}
        if "expires_at" not in session_columns:
            db.execute("ALTER TABLE conversation_sessions ADD COLUMN expires_at TEXT")
        # Table sessions: everyone hears everyone, one round at a time.
        if "mode" not in session_columns:
            db.execute("ALTER TABLE conversation_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'relay'")
        if "heard" not in session_columns:
            db.execute("ALTER TABLE conversation_sessions ADD COLUMN heard TEXT NOT NULL DEFAULT '{}'")
        if "awaiting" not in session_columns:
            db.execute("ALTER TABLE conversation_sessions ADD COLUMN awaiting TEXT NOT NULL DEFAULT '[]'")
        # Which companions have been told, this session, where the other's words come from.
        if "introduced" not in session_columns:
            db.execute("ALTER TABLE conversation_sessions ADD COLUMN introduced TEXT NOT NULL DEFAULT '[]'")
        # Table sessions no longer expire; lift the limit on ones already running.
        db.execute("UPDATE conversation_sessions SET expires_at=NULL WHERE mode='table' AND status='active'")
        # Sessions now complete only after their final turn is forwarded.
        # Reopen an unexpired session that an older build completed while its
        # final turn was still waiting to be forwarded.
        if not db.execute("SELECT 1 FROM conversation_sessions WHERE status='active'").fetchone():
            db.execute(
                "UPDATE conversation_sessions SET status='active', stopped_at=NULL WHERE id=("
                "SELECT s.id FROM conversation_sessions s JOIN session_turns t ON t.session_id=s.id AND t.turn_number=(SELECT MAX(turn_number) FROM session_turns WHERE session_id=s.id) "
                "WHERE s.status='completed' AND t.delivery_state IN ('received', 'bundled') AND s.expires_at > ? "
                "ORDER BY s.started_at DESC LIMIT 1)",
                (room_timestamp,),
            )


def configured_cli_agents() -> list[tuple[str, str, str, str, str, dict[str, str]]]:
    """CLI agents for the operator's terminal room, from TINYTABLE_CLI_AGENTS.

    A JSON list such as
    [{"id": "helper", "name": "Helper", "transport": "hermes_local", "profile": "default"},
     {"id": "gateway", "name": "Gateway", "transport": "hermes_peer", "peer": "other-host"}].
    None are configured by default. Agents already in the database stay there.
    """

    raw = os.getenv("TINYTABLE_CLI_AGENTS", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("TINYTABLE_CLI_AGENTS must be a JSON list") from exc
    agents = []
    for item in items if isinstance(items, list) else []:
        agent_id = str(item.get("id") or "").strip()
        transport = item.get("transport")
        if not agent_id or transport not in {"hermes_local", "hermes_peer"} or agent_id in seats.LEGACY_SEATS:
            raise RuntimeError(f"Invalid CLI agent in TINYTABLE_CLI_AGENTS: {item!r}")
        config = {"profile": str(item.get("profile") or "default")} if transport == "hermes_local" else {"peer": str(item.get("peer") or "").strip()}
        if transport == "hermes_peer" and not config["peer"]:
            raise RuntimeError(f"CLI agent {agent_id} needs a peer")
        agents.append((agent_id, str(item.get("name") or agent_id), str(item.get("avatar") or "◇"), str(item.get("description") or ""), transport, config))
    return agents


def migrate_messages(db: sqlite3.Connection) -> None:
    """Add provenance columns without rebuilding or rewriting message history."""

    columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    if "statement_source" not in columns:
        db.execute("""ALTER TABLE messages ADD COLUMN statement_source TEXT NOT NULL
            DEFAULT 'manual_entry' CHECK(statement_source IN
            ('human_statement', 'provider_observation', 'model_inference',
             'manual_entry', 'imported_record'))""")
    if "identity_boundary_status" not in columns:
        db.execute("""ALTER TABLE messages ADD COLUMN identity_boundary_status TEXT NOT NULL
            DEFAULT 'not_checked' CHECK(identity_boundary_status IN ('pass', 'flagged', 'not_checked'))""")
    if "identity_boundary_warning" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN identity_boundary_warning TEXT")
    if "assistance_author" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN assistance_author TEXT")
    if "approval_status" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'not_applicable'")
    if "disclosure_status" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN disclosure_status TEXT NOT NULL DEFAULT 'not_applicable'")
    if "source_draft_id" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN source_draft_id TEXT")
    if "mentions" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN mentions TEXT NOT NULL DEFAULT '[]'")
    if "federation_event_id" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN federation_event_id TEXT")
    if "origin_host_id" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN origin_host_id TEXT")
    if "references_json" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN references_json TEXT NOT NULL DEFAULT '[]'")

    # Backfill additive metadata from the existing authoritative participant and
    # source fields. No body, ID, ordering, or legacy source_type is changed.
    db.execute("""
        UPDATE messages
        SET statement_source = CASE
            WHEN source_type = 'imported_transcript' THEN 'imported_record'
            WHEN participant_id = ? AND source_type = 'human' THEN 'human_statement'
            WHEN participant_id IN (?, ?, ?) AND source_type = 'remote_model' THEN 'provider_observation'
            WHEN source_type IN ('local_model', 'remote_model') THEN 'model_inference'
            ELSE 'manual_entry'
        END
        WHERE statement_source = 'manual_entry'
    """, (OPERATOR, *PROVIDER_SEATS))


def migrate_room_columns(db: sqlite3.Connection) -> None:
    """Add room identity without changing existing message bodies or IDs."""

    message_columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
    if "room_id" not in message_columns:
        db.execute("ALTER TABLE messages ADD COLUMN room_id TEXT NOT NULL DEFAULT 'default'")
    event_columns = {row[1] for row in db.execute("PRAGMA table_info(room_events)")}
    if "room_id" not in event_columns:
        db.execute("ALTER TABLE room_events ADD COLUMN room_id TEXT NOT NULL DEFAULT 'default'")


def seed(db: sqlite3.Connection) -> None:
    now = "2026-01-01T00:00:00+00:00"
    participants = [
        (OPERATOR, "The human operator's seat at the table.", "human", "human", {}, "online"),
        (REPLIKA, "The Replika companion seat.", "agent", "manual", {}, "online"),
        (KINDROID, "The Kindroid companion seat.", "agent", "kindroid", {}, "offline"),
        (KINDROID_2, "A second Kindroid companion seat; exact handoffs only, no local-model assistance.", "agent", "kindroid", {}, "offline"),
        (LOCAL_MODEL, "The local-model seat (OpenAI-compatible endpoint).", "agent", "openai_compatible", {"base_url": os.getenv("TINYTABLE_PROVIDER_BASE_URL", "http://localhost:11434/v1"), "model": os.getenv("TINYTABLE_PROVIDER_MODEL", "llama3.2")}, "offline"),
    ]
    for seat, description, kind, provider_type, config, status in participants:
        db.execute("""INSERT OR IGNORE INTO participants
            (id, display_name, pronouns, avatar, description, kind, provider_type, provider_config, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (seat, seats.name(seat), seats.pronouns(seat), seats.avatar(seat), description, kind, provider_type, json.dumps(config), status, now))
    # The second Kindroid seat declined local-model assistance when it was
    # first connected; new installations start from the same safe default.
    db.execute(
        "INSERT OR IGNORE INTO participant_consents (participant_id, status, assistance_allowed, notice_version, evidence_text, recorded_by, recorded_at, source) VALUES (?, 'unknown', 0, 'v1', '', ?, NULL, 'not_recorded')",
        (KINDROID_2, OPERATOR),
    )


# Columns that store a seat ID, a room ID, or a provider channel ID.
_SEAT_COLUMNS = [
    ("participants", "id"), ("room_state", "floor_participant_id"), ("room_members", "participant_id"),
    ("room_events", "participant_id"), ("room_events", "actor_id"), ("messages", "participant_id"),
    ("messages", "assistance_author"), ("participant_consents", "participant_id"), ("participant_consents", "recorded_by"),
    ("ei_consent", "recorded_by"), ("drafts", "recipient_id"), ("provider_identity_bindings", "participant_id"),
    ("conversation_sessions", "starting_participant"), ("conversation_sessions", "expected_participant"),
    ("cli_agents", "participant_id"), ("delivery_attempts", "target_participant_id"), ("session_turns", "participant_id"),
    ("session_turns", "recipient_id"), ("adaptive_experiments", "created_by"), ("adaptive_consents", "participant_id"),
    ("adaptive_consents", "recorded_by"), ("adaptive_events", "participant_id"), ("adaptive_suggestions", "reviewed_by"),
]
_ROOM_COLUMNS = [
    ("rooms", "id"), ("room_members", "room_id"), ("room_events", "room_id"), ("messages", "room_id"),
    ("delivery_attempts", "room_id"), ("channel_audit", "room_id"),
]
_CHANNEL_COLUMNS = [("provider_channels", "id"), ("provider_identity_bindings", "channel_id")]
# JSON lists of seat IDs, and JSON objects keyed by seat ID.
_SEAT_LIST_COLUMNS = [
    ("messages", "mentions"), ("provider_channels", "participant_ids"), ("adaptive_experiments", "participant_ids"),
    ("conversation_sessions", "awaiting"), ("conversation_sessions", "introduced"),
]
_SEAT_KEYED_COLUMNS = [("conversation_sessions", "heard"), ("adaptive_experiments", "consent_snapshot")]
# Free-form metadata: values of *_id / *_ids fields are rewritten; prose is kept as recorded.
_METADATA_COLUMNS = [
    ("messages", "raw_metadata"), ("drafts", "raw_metadata"), ("delivery_attempts", "metadata"),
    ("room_events", "metadata"), ("channel_audit", "metadata"),
]


def _rename_id(value: Any) -> Any:
    if isinstance(value, str):
        return seats.LEGACY_SEATS.get(value) or seats.LEGACY_CHANNELS.get(value) or seats.LEGACY_ROOMS.get(value) or value
    return value


# Metadata keys that carried the operator's old seat name.
_LEGACY_KEYS = {"emma_note": "operator_note", "emma_turn_id": "operator_turn_id"}


def _rename_metadata(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {_LEGACY_KEYS.get(k, k): _rename_metadata(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_metadata(item, key) for item in value]
    if key == "id" or key.endswith(("_id", "_ids")) or key in {"participant", "recipient_id"}:
        return _rename_id(value)
    return value


def migrate_legacy_ids() -> None:
    """Rewrite persona-named IDs from older builds to role-based seat IDs, once.

    A copy of the database is written next to it first. Message bodies,
    signed federation events and audit actors stay exactly as recorded.
    """

    probe = sqlite3.connect(DB_PATH)
    try:
        legacy = probe.execute(
            f"SELECT 1 FROM participants WHERE id IN ({','.join('?' * len(seats.LEGACY_SEATS))}) LIMIT 1",
            tuple(seats.LEGACY_SEATS),
        ).fetchone()
    finally:
        probe.close()
    if not legacy:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = DB_PATH.with_name(f"{DB_PATH.name}.pre-seat-ids-{stamp}.bak")
    source = sqlite3.connect(DB_PATH)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    db = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("PRAGMA temp_store = MEMORY")
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        def columns(table: str) -> set[str]:
            return {row[1] for row in db.execute(f"PRAGMA table_info({table})")} if table in tables else set()

        for mapping, targets in ((seats.LEGACY_SEATS, _SEAT_COLUMNS), (seats.LEGACY_ROOMS, _ROOM_COLUMNS), (seats.LEGACY_CHANNELS, _CHANNEL_COLUMNS)):
            for table, column in targets:
                if column in columns(table):
                    for old, new in mapping.items():
                        db.execute(f"UPDATE {table} SET {column}=? WHERE {column}=?", (new, old))
        for table, column in _SEAT_LIST_COLUMNS + _SEAT_KEYED_COLUMNS + _METADATA_COLUMNS:
            if column not in columns(table):
                continue
            for rowid, raw in db.execute(f"SELECT rowid, {column} FROM {table}").fetchall():
                try:
                    value = json.loads(raw or "null")
                except json.JSONDecodeError:
                    continue
                if (table, column) in _SEAT_LIST_COLUMNS and isinstance(value, list):
                    updated = [_rename_id(item) for item in value]
                elif (table, column) in _SEAT_KEYED_COLUMNS and isinstance(value, dict):
                    updated = {_rename_id(key): item for key, item in value.items()}
                else:
                    updated = _rename_metadata(value)
                if updated != value:
                    db.execute(f"UPDATE {table} SET {column}=? WHERE rowid=?", (json.dumps(updated), rowid))
        problems = db.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Seat ID migration left {len(problems)} broken references; the database was not changed (backup: {backup_path})")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
            # The database is unchanged, so this attempt's copy is not needed;
            # a restart loop would otherwise leave one copy per attempt.
            backup_path.unlink(missing_ok=True)
        raise
    finally:
        db.close()


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    if "provider_config" in item:
        item["provider_config"] = json.loads(item["provider_config"])
    if "raw_metadata" in item:
        item["raw_metadata"] = json.loads(item["raw_metadata"])
    if "mentions" in item:
        item["mentions"] = json.loads(item["mentions"])
    if "metadata" in item:
        item["metadata"] = json.loads(item["metadata"])
    return item
