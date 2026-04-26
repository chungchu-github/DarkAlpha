-- Phase 4: signal journal and post-signal outcome tracking

CREATE TABLE IF NOT EXISTS signal_journal (
    event_id            TEXT PRIMARY KEY REFERENCES setup_events(event_id),
    timestamp           TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    direction           TEXT,
    ranking_score       REAL NOT NULL,
    entry_price         REAL,
    stop_price          REAL,
    take_profit_price   REAL,
    position_usdt       REAL,
    max_risk_usdt       REAL,
    leverage_suggest    REAL,
    ttl_minutes         INTEGER,
    invalid_condition   TEXT,
    risk_level          TEXT,
    data_health_status  TEXT,
    data_health_reason  TEXT,
    raw_payload         TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signal_journal_symbol
    ON signal_journal(symbol);

CREATE INDEX IF NOT EXISTS idx_signal_journal_strategy
    ON signal_journal(strategy);

CREATE INDEX IF NOT EXISTS idx_signal_journal_timestamp
    ON signal_journal(timestamp);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL REFERENCES signal_journal(event_id),
    horizon           TEXT NOT NULL CHECK(horizon IN ('5m','15m','1h','4h')),
    observed_at       TEXT,
    mark_price        REAL,
    return_pct        REAL,
    r_multiple        REAL,
    max_favorable_pct REAL,
    max_adverse_pct   REAL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','observed','failed')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(event_id, horizon)
);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_event
    ON signal_outcomes(event_id);

CREATE INDEX IF NOT EXISTS idx_signal_outcomes_status
    ON signal_outcomes(status);
