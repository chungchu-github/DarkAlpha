-- Gate 6.3: live user data stream event journal and de-duplication

CREATE TABLE IF NOT EXISTS live_stream_events (
    event_id         TEXT PRIMARY KEY,
    event_type       TEXT NOT NULL,
    symbol           TEXT,
    client_order_id  TEXT,
    execution_type   TEXT,
    order_status     TEXT,
    trade_id         TEXT,
    payload          TEXT NOT NULL,
    processed_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_live_stream_events_client_order
    ON live_stream_events(client_order_id);

CREATE INDEX IF NOT EXISTS idx_live_stream_events_symbol
    ON live_stream_events(symbol);
