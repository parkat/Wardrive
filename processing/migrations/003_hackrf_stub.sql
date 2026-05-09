-- Migration 003: HackRF observation table stub

CREATE TABLE IF NOT EXISTS hackrf_obs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT REFERENCES sessions(session_id),
    ts           REAL NOT NULL,
    freq_hz      INTEGER NOT NULL,
    power_db     REAL,
    bandwidth_hz INTEGER,
    lat          REAL,
    lon          REAL,
    alt_m        REAL
);
CREATE INDEX IF NOT EXISTS idx_hackrf_session ON hackrf_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_hackrf_freq    ON hackrf_obs(freq_hz);

INSERT OR IGNORE INTO schema_version (version) VALUES (3);
