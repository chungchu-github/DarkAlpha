-- Phase 0 schema — all four tables from spec Section 5.2

CREATE TABLE IF NOT EXISTS setup_events (
    event_id    TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    setup_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,   -- full SetupEvent JSON
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_tickets (
    ticket_id       TEXT PRIMARY KEY,
    source_event_id TEXT REFERENCES setup_events(event_id),
    status          TEXT NOT NULL CHECK(status IN (
                        'created','accepted','rejected','expired','filled','closed'
                    )),
    shadow_mode     INTEGER NOT NULL DEFAULT 1,  -- 1=shadow, 0=live
    payload         TEXT NOT NULL,               -- full ExecutionTicket JSON
    created_at      TEXT,
    decided_at      TEXT,
    reject_reason   TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    order_id          TEXT PRIMARY KEY,
    ticket_id         TEXT REFERENCES execution_tickets(ticket_id),
    exchange_order_id TEXT,
    side              TEXT,
    type              TEXT,
    symbol            TEXT,
    price             REAL,
    quantity          REAL,
    status            TEXT,
    submitted_at      TEXT,
    filled_at         TEXT,
    fill_price        REAL,
    fill_quantity     REAL,
    fee_usd           REAL
);

CREATE TABLE IF NOT EXISTS trades (
    trade_id         TEXT PRIMARY KEY,
    symbol           TEXT,
    direction        TEXT,
    entry_order_id   TEXT REFERENCES orders(order_id),
    exit_order_id    TEXT REFERENCES orders(order_id),
    entry_time       TEXT,
    exit_time        TEXT,
    entry_price      REAL,
    exit_price       REAL,
    quantity         REAL,
    gross_pnl_usd    REAL,
    fees_usd         REAL,
    net_pnl_usd      REAL,
    shadow_mode      INTEGER NOT NULL DEFAULT 1,
    source_event_id  TEXT REFERENCES setup_events(event_id),
    exit_reason      TEXT  -- stop_loss | take_profit | manual | kill_switch | invalidation
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    date                  TEXT PRIMARY KEY,
    starting_equity       REAL,
    ending_equity         REAL,
    trade_count           INTEGER,
    win_count             INTEGER,
    loss_count            INTEGER,
    gross_pnl             REAL,
    fees                  REAL,
    net_pnl               REAL,
    max_drawdown_intraday REAL,
    mode                  TEXT,
    gate                  TEXT
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_setup_events_symbol   ON setup_events(symbol);
CREATE INDEX IF NOT EXISTS idx_setup_events_ts       ON setup_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_tickets_status        ON execution_tickets(status);
CREATE INDEX IF NOT EXISTS idx_trades_shadow         ON trades(shadow_mode);
CREATE INDEX IF NOT EXISTS idx_trades_symbol         ON trades(symbol);
