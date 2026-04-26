-- Phase 5 readiness: live order idempotency and reconciliation records

CREATE TABLE IF NOT EXISTS order_idempotency (
    client_order_id TEXT PRIMARY KEY,
    ticket_id       TEXT NOT NULL REFERENCES execution_tickets(ticket_id),
    order_role      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        REAL NOT NULL,
    price           REAL,
    status          TEXT NOT NULL DEFAULT 'reserved'
                   CHECK(status IN ('reserved','submitted','acknowledged','rejected','cancelled','filled')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_order_idempotency_ticket
    ON order_idempotency(ticket_id);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id      TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK(status IN ('started','ok','mismatch','failed')),
    details     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
