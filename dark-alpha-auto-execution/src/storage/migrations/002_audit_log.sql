-- Phase 1: audit trail and circuit breaker state persistence

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT NOT NULL,   -- signal_received | signal_rejected | kill_switch_activated
                                 -- | kill_switch_cleared | circuit_breaker_tripped
                                 -- | circuit_breaker_cleared
    event_id    TEXT,            -- setup_event / ticket_id the decision relates to (nullable)
    source      TEXT NOT NULL,   -- which module generated this entry
    decision    TEXT NOT NULL,   -- accept | reject | activate | clear | trip | reset
    reason      TEXT,
    metadata    TEXT,            -- JSON blob for extra context
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS circuit_breaker_state (
    name        TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'ok'
                    CHECK(status IN ('ok', 'tripped')),
    reason      TEXT,
    action      TEXT,            -- halt_24h | halt_12h | halt_until_manual_reset
                                 -- | no_new_entries | halve_position_size
    tripped_at  TEXT,
    clear_at    TEXT             -- ISO8601 UTC — when auto-clear fires (NULL = manual only)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
