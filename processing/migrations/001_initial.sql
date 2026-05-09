-- Migration 001: initial schema (matches existing wardrive.db)
-- This runs only if schema_version table doesn't have version=1.
-- All CREATE TABLE IF NOT EXISTS so it's safe to run against an existing DB.

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    started_at_utc TEXT,
    ended_at_utc   TEXT,
    hostname       TEXT,
    label          TEXT
);

CREATE TABLE IF NOT EXISTS bt_devices (
    mac     TEXT PRIMARY KEY,
    name    TEXT,
    vendor  TEXT,
    device_type TEXT
);

CREATE TABLE IF NOT EXISTS bt_obs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(session_id),
    mac        TEXT REFERENCES bt_devices(mac),
    ts         TEXT,
    rssi       INTEGER,
    lat        REAL,
    lon        REAL,
    alt_m      REAL
);

CREATE TABLE IF NOT EXISTS wifi_aps (
    bssid      TEXT PRIMARY KEY,
    ssid       TEXT,
    vendor     TEXT,
    encryption TEXT,
    channel    INTEGER
);

CREATE TABLE IF NOT EXISTS wifi_obs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(session_id),
    bssid      TEXT REFERENCES wifi_aps(bssid),
    ts         TEXT,
    rssi       INTEGER,
    lat        REAL,
    lon        REAL
);

CREATE TABLE IF NOT EXISTS wifi_clients (
    mac        TEXT PRIMARY KEY,
    vendor     TEXT,
    device_type TEXT
);

CREATE TABLE IF NOT EXISTS rf_devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    model      TEXT,
    protocol   TEXT,
    channel    INTEGER
);

CREATE TABLE IF NOT EXISTS rf_obs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(session_id),
    rf_device_id INTEGER REFERENCES rf_devices(id),
    ts         TEXT,
    rssi       REAL,
    lat        REAL,
    lon        REAL
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
