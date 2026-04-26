-- Gate 6.4-6.8: runtime heartbeats and readiness review records

CREATE TABLE IF NOT EXISTS live_runtime_heartbeats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    component   TEXT NOT NULL,
    status      TEXT NOT NULL,
    details     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_live_runtime_heartbeats_component
    ON live_runtime_heartbeats(component);

CREATE INDEX IF NOT EXISTS idx_live_runtime_heartbeats_created_at
    ON live_runtime_heartbeats(created_at);

CREATE TABLE IF NOT EXISTS gate6_readiness_reports (
    report_id   TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK(status IN ('go','no_go')),
    details     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
