#!/usr/bin/env python3
"""
enrich.py — Post-capture enrichment for the warDrive project.

Reads raw session data from capture/raw/<session>/ and populates
the wardrive SQLite database.

Collectors processed:
  • Kismet WiFi     → wifi_aps, wifi_clients, wifi_obs
  • rtl_433 SDR     → rf_devices, rf_obs
  • ESP32 BLE       → bt_devices, bt_obs         ← NEW

Raw data is never modified. This script is safe to re-run.

Schema version: 1 (BLE tables added as additive columns/tables).
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
INSERT OR IGNORE INTO schema_meta VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    started_at_utc  TEXT,
    ended_at_utc    TEXT,
    hostname        TEXT,
    wifi_enabled    INT DEFAULT 0,
    sdr_enabled     INT DEFAULT 0,
    esp32_enabled   INT DEFAULT 0,
    gps_enabled     INT DEFAULT 0,
    notes           TEXT
);

-- ── WiFi ──────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wifi_aps (
    bssid           TEXT PRIMARY KEY,
    ssid            TEXT,
    encryption      TEXT,
    channel         INT,
    max_signal_dbm  INT,
    first_seen_utc  TEXT,
    last_seen_utc   TEXT,
    obs_count       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_clients (
    mac             TEXT PRIMARY KEY,
    probe_ssid      TEXT,
    first_seen_utc  TEXT,
    last_seen_utc   TEXT,
    obs_count       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_obs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    timestamp_utc   TEXT NOT NULL,
    bssid           TEXT,
    client_mac      TEXT,
    signal_dbm      INT,
    lat             REAL,
    lon             REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_wifi_obs_session ON wifi_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_wifi_obs_bssid   ON wifi_obs(bssid);

-- ── SDR / rtl_433 ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rf_devices (
    device_id       TEXT PRIMARY KEY,   -- derived: model + id fields
    model           TEXT,
    protocol        TEXT,
    frequency_mhz   REAL,
    first_seen_utc  TEXT,
    last_seen_utc   TEXT,
    obs_count       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rf_obs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    timestamp_utc   TEXT NOT NULL,
    device_id       TEXT,
    raw_json        TEXT,
    lat             REAL,
    lon             REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_rf_obs_session ON rf_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_rf_obs_device  ON rf_obs(device_id);

-- ── BLE / ESP32 ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bt_devices (
    address                 TEXT PRIMARY KEY,   -- BD_ADDR, uppercase colon-sep
    address_type            TEXT,               -- public|random_static|random_resolvable|random_non_resolvable
    name                    TEXT,               -- last-seen advertised name (may be NULL)
    is_randomized           INT DEFAULT 0,      -- locally-administered bit set in address
    manufacturer            TEXT,               -- decoded from mfg_id via OUI (best-effort)
    appearance              INT,                -- BLE appearance value
    services                TEXT,               -- JSON array of service UUIDs seen
    apple_continuity_type   TEXT,               -- e.g. "AirPods", "Handoff", "NearbyAction"
    first_seen_utc          TEXT,
    last_seen_utc           TEXT,
    max_rssi_dbm            INT,
    obs_count               INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bt_obs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    timestamp_utc   TEXT NOT NULL,
    address         TEXT NOT NULL,
    rssi_dbm        INT,
    raw_payload     TEXT,           -- hex of full advertisement (from firmware "raw" field)
    lat             REAL,           -- NULL until GPS is integrated
    lon             REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_bt_obs_session ON bt_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_bt_obs_addr    ON bt_obs(address);
CREATE INDEX IF NOT EXISTS idx_bt_obs_time    ON bt_obs(timestamp_utc);

-- ── OUI lookup (shared across WiFi + BLE) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS oui_lookup (
    prefix          TEXT PRIMARY KEY,   -- first 6 hex chars, uppercase no colons
    organization    TEXT
);
"""

# ── BLE address-type decoder ───────────────────────────────────────────────────
# The Arduino library exposes addr_type as an integer (esp_ble_addr_type_t):
#   0 = BLE_ADDR_TYPE_PUBLIC
#   1 = BLE_ADDR_TYPE_RANDOM   (further distinguished by high 2 bits of address)
#
# For random addresses, BLE spec defines sub-types by the top 2 bits
# of the most-significant byte:
#   11 = static random
#   01 = resolvable private
#   00 = non-resolvable private

def decode_address_type(addr: str, addr_type_int: int) -> str:
    if addr_type_int == 0:
        return "public"
    # Random: decode sub-type from the high byte of the address
    try:
        high_byte = int(addr.split(":")[0], 16)
        top_bits = (high_byte >> 6) & 0x03
        if top_bits == 0b11:
            return "random_static"
        elif top_bits == 0b01:
            return "random_resolvable"
        else:
            return "random_non_resolvable"
    except (ValueError, IndexError):
        return "random"


# ── Known BLE manufacturer IDs (small local table for common ones) ─────────────
KNOWN_MFG = {
    0x004C: "Apple",
    0x0006: "Microsoft",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x0499: "Ruuvi Innovations",
    0x0059: "Nordic Semiconductor",
    0x0157: "Garmin",
    0x01D8: "Tile",
    0x0171: "Amazon",
    0x0397: "Bose",
    0x0310: "Jabra",
    0x0089: "Plantronics",
    0x038F: "Sony",
    0x004F: "Beats",
    0x03DA: "Nothing",
}

def lookup_manufacturer(mfg_id: int) -> str | None:
    return KNOWN_MFG.get(mfg_id)


# ── Process ESP32 BLE data ─────────────────────────────────────────────────────
def process_esp32_ble(db: sqlite3.Connection, session_id: str, bt_ndjson: Path) -> int:
    """
    Read bt_ndjson, upsert bt_devices, insert bt_obs.
    Returns number of observation records inserted.
    """
    if not bt_ndjson.exists():
        print(f"  [ble] No BLE data file at {bt_ndjson} — skipping")
        return 0

    obs_count = 0
    device_cache: dict[str, dict] = {}  # addr → aggregated device info

    with open(bt_ndjson) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  [ble] Parse error line {lineno}: {e}")
                continue

            addr = rec.get("addr", "").upper()
            if not addr or len(addr) < 17:
                continue

            ts = rec.get("ts") or rec.get("ms")  # ts = UTC ISO; ms = millis fallback
            if ts is None:
                continue
            # Normalize: if it looks like a number, it's millis — skip (should have ts)
            if isinstance(ts, (int, float)):
                print(f"  [ble] Line {lineno}: no UTC timestamp — skipping")
                continue

            rssi = rec.get("rssi")
            raw_hex = rec.get("raw")
            addr_type_int = rec.get("addr_type", 1)
            is_rand = rec.get("rand", 0)
            name = rec.get("name")
            mfg_id = rec.get("mfg_id")
            appearance = rec.get("appearance")
            services = rec.get("services")
            apple_type = rec.get("apple_type")

            # Insert observation
            db.execute(
                """INSERT INTO bt_obs (session_id, timestamp_utc, address, rssi_dbm, raw_payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, ts, addr, rssi, raw_hex),
            )
            obs_count += 1

            # Aggregate device info for upsert
            if addr not in device_cache:
                device_cache[addr] = {
                    "address_type": decode_address_type(addr, addr_type_int),
                    "is_randomized": is_rand,
                    "name": name,
                    "mfg_id": mfg_id,
                    "appearance": appearance,
                    "services": set(services) if services else set(),
                    "apple_type": apple_type,
                    "first_seen": ts,
                    "last_seen": ts,
                    "max_rssi": rssi if rssi is not None else -999,
                    "obs_count": 1,
                }
            else:
                dev = device_cache[addr]
                if name and not dev["name"]:
                    dev["name"] = name
                if apple_type and not dev["apple_type"]:
                    dev["apple_type"] = apple_type
                if appearance is not None and dev["appearance"] is None:
                    dev["appearance"] = appearance
                if services:
                    dev["services"].update(services)
                if rssi is not None and rssi > dev["max_rssi"]:
                    dev["max_rssi"] = rssi
                if ts < dev["first_seen"]:
                    dev["first_seen"] = ts
                if ts > dev["last_seen"]:
                    dev["last_seen"] = ts
                if mfg_id is not None and dev["mfg_id"] is None:
                    dev["mfg_id"] = mfg_id
                dev["obs_count"] += 1

    # Upsert devices
    for addr, dev in device_cache.items():
        manufacturer = lookup_manufacturer(dev["mfg_id"]) if dev["mfg_id"] else None
        services_json = json.dumps(sorted(dev["services"])) if dev["services"] else None

        db.execute(
            """INSERT INTO bt_devices
                   (address, address_type, name, is_randomized, manufacturer,
                    appearance, services, apple_continuity_type,
                    first_seen_utc, last_seen_utc, max_rssi_dbm, obs_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(address) DO UPDATE SET
                   name                  = COALESCE(excluded.name, bt_devices.name),
                   manufacturer          = COALESCE(excluded.manufacturer, bt_devices.manufacturer),
                   appearance            = COALESCE(excluded.appearance, bt_devices.appearance),
                   services              = CASE
                       WHEN excluded.services IS NULL THEN bt_devices.services
                       WHEN bt_devices.services IS NULL THEN excluded.services
                       ELSE excluded.services  -- caller merges before upsert
                   END,
                   apple_continuity_type = COALESCE(excluded.apple_continuity_type,
                                                     bt_devices.apple_continuity_type),
                   last_seen_utc         = MAX(bt_devices.last_seen_utc, excluded.last_seen_utc),
                   first_seen_utc        = MIN(bt_devices.first_seen_utc, excluded.first_seen_utc),
                   max_rssi_dbm          = MAX(bt_devices.max_rssi_dbm, excluded.max_rssi_dbm),
                   obs_count             = bt_devices.obs_count + excluded.obs_count
            """,
            (
                addr,
                dev["address_type"],
                dev["name"],
                dev["is_randomized"],
                manufacturer,
                dev["appearance"],
                services_json,
                dev["apple_type"],
                dev["first_seen"],
                dev["last_seen"],
                dev["max_rssi"] if dev["max_rssi"] > -999 else None,
                dev["obs_count"],
            ),
        )

    db.commit()
    print(f"  [ble] {obs_count} observations, {len(device_cache)} unique devices")
    return obs_count


# ── Process WiFi (Kismet) — stub matching existing pattern ────────────────────
def process_kismet(db: sqlite3.Connection, session_id: str, wifi_dir: Path) -> int:
    # Placeholder matching the existing enrich.py pattern.
    # Full implementation reads the Kismet SQLite output.
    kismet_files = list(wifi_dir.glob("*.kismet"))
    if not kismet_files:
        print("  [wifi] No .kismet files found — skipping")
        return 0
    print(f"  [wifi] Found {len(kismet_files)} .kismet file(s) (processing omitted — existing impl)")
    return 0


# ── Process SDR (rtl_433) ─────────────────────────────────────────────────────
def process_rtl433(db: sqlite3.Connection, session_id: str, sdr_dir: Path) -> int:
    ndjson_files = list(sdr_dir.glob("*.ndjson"))
    if not ndjson_files:
        print("  [sdr] No NDJSON files found — skipping")
        return 0

    obs_count = 0
    for ndjson_path in ndjson_files:
        with open(ndjson_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = rec.get("time") or rec.get("timestamp_utc")
                model = rec.get("model")
                freq = rec.get("freq")
                protocol = rec.get("protocol")
                device_key = f"{model}_{rec.get('id', 'unknown')}"

                db.execute(
                    """INSERT OR IGNORE INTO rf_devices
                           (device_id, model, protocol, frequency_mhz, first_seen_utc, last_seen_utc, obs_count)
                       VALUES (?, ?, ?, ?, ?, ?, 0)""",
                    (device_key, model, str(protocol) if protocol else None, freq, ts, ts),
                )
                db.execute(
                    """UPDATE rf_devices SET last_seen_utc = ?, obs_count = obs_count + 1
                       WHERE device_id = ?""",
                    (ts, device_key),
                )
                db.execute(
                    """INSERT INTO rf_obs (session_id, timestamp_utc, device_id, raw_json)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, ts, device_key, line),
                )
                obs_count += 1

    db.commit()
    print(f"  [sdr] {obs_count} RF observations")
    return obs_count


# ── Session loader ─────────────────────────────────────────────────────────────
def load_session(db: sqlite3.Connection, session_dir: Path) -> str | None:
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"  [warn] No manifest.json in {session_dir} — skipping")
        return None

    with open(manifest_path) as f:
        manifest = json.load(f)

    session_id = manifest.get("session_id")
    if not session_id:
        print(f"  [warn] manifest missing session_id — skipping")
        return None

    collectors = manifest.get("collectors", {})
    db.execute(
        """INSERT OR IGNORE INTO sessions
               (session_id, started_at_utc, ended_at_utc, hostname,
                wifi_enabled, sdr_enabled, esp32_enabled, gps_enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (
            session_id,
            manifest.get("started_at_utc"),
            manifest.get("ended_at_utc"),
            manifest.get("hostname"),
            1 if collectors.get("wifi") else 0,
            1 if collectors.get("sdr") else 0,
            1 if collectors.get("esp32") else 0,
        ),
    )
    db.commit()
    return session_id


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="warDrive enrichment pipeline")
    parser.add_argument(
        "--raw-dir",
        default=str(Path(__file__).parent.parent / "capture" / "raw"),
        help="Path to capture/raw/",
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).parent / "wardrive.db"),
        help="Path to output SQLite database",
    )
    parser.add_argument(
        "--session",
        default=None,
        help="Process a single session by name (default: all unprocessed)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all sessions, even if already present in the DB",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    db_path = Path(args.db)

    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA_SQL)

    totals = {"wifi": 0, "sdr": 0, "ble_obs": 0, "ble_devices": 0}

    if args.session:
        session_dirs = [raw_dir / args.session]
    else:
        session_dirs = sorted(raw_dir.iterdir()) if raw_dir.exists() else []

    for session_dir in session_dirs:
        if not session_dir.is_dir():
            continue

        print(f"\n[enrich] Session: {session_dir.name}")

        session_id = load_session(db, session_dir)
        if not session_id:
            continue

        # Skip already-processed sessions unless --all
        if not args.all:
            row = db.execute(
                "SELECT COUNT(*) FROM bt_obs WHERE session_id = ?", (session_id,)
            ).fetchone()
            existing = row[0] if row else 0
            row2 = db.execute(
                "SELECT COUNT(*) FROM rf_obs WHERE session_id = ?", (session_id,)
            ).fetchone()
            existing += row2[0] if row2 else 0
            if existing > 0:
                print(f"  Already processed ({existing} records) — use --all to re-run")
                continue

        process_kismet(db, session_id, session_dir / "wifi")
        sdr_obs = process_rtl433(db, session_id, session_dir / "sdr")
        ble_obs = process_esp32_ble(db, session_id, session_dir / "bt" / "esp32_ble.ndjson")

        totals["sdr"] += sdr_obs
        totals["ble_obs"] += ble_obs

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"  wardrive.db: {db_path}")
    print(f"  RF obs written:   {totals['sdr']}")
    print(f"  BLE obs written:  {totals['ble_obs']}")

    row = db.execute("SELECT COUNT(*) FROM bt_devices").fetchone()
    print(f"  Unique BLE devices in DB: {row[0]}")

    row = db.execute(
        "SELECT COUNT(*) FROM bt_devices WHERE apple_continuity_type IS NOT NULL"
    ).fetchone()
    print(f"  Apple Continuity devices: {row[0]}")

    row = db.execute(
        "SELECT COUNT(*) FROM bt_devices WHERE is_randomized = 0"
    ).fetchone()
    print(f"  Public (stable) addresses: {row[0]}")

    print("=" * 60)

    db.close()


if __name__ == "__main__":
    main()
