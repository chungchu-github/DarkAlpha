-- Phase 3: positions table + equity snapshots for paper broker

CREATE TABLE IF NOT EXISTS positions (
    position_id       TEXT PRIMARY KEY,
    ticket_id         TEXT REFERENCES execution_tickets(ticket_id),
    symbol            TEXT NOT NULL,
    direction         TEXT NOT NULL CHECK(direction IN ('long','short')),
    status            TEXT NOT NULL CHECK(status IN (
                          'pending','open','partial','closed','cancelled'
                      )) DEFAULT 'pending',
    entry_price       REAL,
    exit_price        REAL,
    quantity          REAL NOT NULL,
    filled_quantity   REAL NOT NULL DEFAULT 0,
    stop_price        REAL,
    take_profit_price REAL,
    opened_at         TEXT,
    closed_at         TEXT,
    exit_reason       TEXT,          -- stop_loss | take_profit | manual | kill_switch | invalidation
    gross_pnl_usd     REAL,
    fees_usd          REAL,
    net_pnl_usd       REAL,
    shadow_mode       INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_positions_status     ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol     ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_positions_ticket     ON positions(ticket_id);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL DEFAULT (datetime('now')),
    equity_usd REAL NOT NULL,
    realized   REAL NOT NULL DEFAULT 0,
    unrealized REAL NOT NULL DEFAULT 0,
    mode       TEXT NOT NULL,
    gate       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_snapshots_ts ON equity_snapshots(ts);
