-- Migration 002: supervisor event tables and indexes

CREATE TABLE IF NOT EXISTS collector_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    collector_id TEXT NOT NULL,
    event_type   TEXT NOT NULL CHECK(event_type IN
                   ('start','stop','crash','reconnect','power_event','disabled','enabled')),
    details      TEXT  -- JSON
);
CREATE INDEX IF NOT EXISTS idx_ce_ts        ON collector_events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_ce_collector ON collector_events(collector_id, ts DESC);

CREATE TABLE IF NOT EXISTS power_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    port       TEXT NOT NULL,
    milliamps  INTEGER,
    event_type TEXT NOT NULL CHECK(event_type IN
                  ('overcurrent','budget_exceeded','port_cycle'))
);
CREATE INDEX IF NOT EXISTS idx_pe_ts ON power_events(ts DESC);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
