#!/usr/bin/env python3
"""
enrich.py — post-capture processor for wardriving sessions

Reads a raw session directory and produces:
  - SQLite database with normalized tables (for the future web app)
  - Enriched NDJSON (one record per observation, schema_version-tagged)
  - Summary report (counts, unique devices, frequency activity)

Designed to be re-runnable: deletes and rebuilds the enriched output every
time, so you can improve enrichment logic and reprocess old captures.

Schema (v1):
  sessions     — one row per capture session
  wifi_aps     — unique access points seen (BSSID is key)
  wifi_clients — unique client devices seen (probe requests, etc.)
  wifi_obs     — every individual WiFi observation (time-series)
  rf_devices   — unique RF devices from rtl_433 (model + id is key)
  rf_obs       — every individual RF observation (time-series)
  oui_lookup   — MAC vendor lookup cache

Usage:
  ./enrich.py <session_dir>
  ./enrich.py --all          # process every session under capture/raw/
  ./enrich.py --db <path>    # custom database path
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 1

# --- Logging -----------------------------------------------------------------
logging.basicConfig(
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
log = logging.getLogger("enrich")


# --- Database schema ---------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    ended_at_utc   TEXT,
    hostname       TEXT,
    wifi_enabled   INTEGER,
    sdr_enabled    INTEGER,
    gps_enabled    INTEGER,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS wifi_aps (
    bssid          TEXT PRIMARY KEY,
    last_ssid      TEXT,
    vendor         TEXT,
    encryption     TEXT,
    channel        INTEGER,
    frequency_mhz  INTEGER,
    first_seen_utc TEXT,
    last_seen_utc  TEXT,
    max_signal_dbm INTEGER,
    obs_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_clients (
    mac            TEXT PRIMARY KEY,
    vendor         TEXT,
    is_randomized  INTEGER,
    probed_ssids   TEXT,         -- JSON array
    first_seen_utc TEXT,
    last_seen_utc  TEXT,
    obs_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS wifi_obs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    timestamp_utc  TEXT NOT NULL,
    obs_type       TEXT NOT NULL,    -- 'beacon', 'probe_req', 'probe_resp', etc.
    bssid          TEXT,
    client_mac     TEXT,
    ssid           TEXT,
    signal_dbm     INTEGER,
    channel        INTEGER,
    lat            REAL,
    lon            REAL,
    raw_json       TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_wifi_obs_session ON wifi_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_wifi_obs_bssid   ON wifi_obs(bssid);
CREATE INDEX IF NOT EXISTS idx_wifi_obs_time    ON wifi_obs(timestamp_utc);

CREATE TABLE IF NOT EXISTS rf_devices (
    device_key     TEXT PRIMARY KEY, -- "<model>:<id>"
    model          TEXT NOT NULL,
    device_id      TEXT,
    protocol       INTEGER,
    typical_freq_mhz REAL,
    first_seen_utc TEXT,
    last_seen_utc  TEXT,
    obs_count      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rf_obs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    timestamp_utc  TEXT NOT NULL,
    device_key     TEXT,
    model          TEXT,
    protocol       INTEGER,
    freq_mhz       REAL,
    rssi_db        REAL,
    snr_db         REAL,
    lat            REAL,
    lon            REAL,
    raw_json       TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_rf_obs_session ON rf_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_rf_obs_device  ON rf_obs(device_key);
CREATE INDEX IF NOT EXISTS idx_rf_obs_time    ON rf_obs(timestamp_utc);

CREATE TABLE IF NOT EXISTS oui_lookup (
    oui    TEXT PRIMARY KEY,   -- first 6 hex chars, uppercase, no separators
    vendor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# --- Helpers -----------------------------------------------------------------
def normalize_mac(mac: str) -> str:
    """Uppercase, colon-separated MAC."""
    if not mac:
        return ""
    return mac.upper().replace("-", ":")


def mac_oui(mac: str) -> str:
    """Return first 6 hex chars of a MAC, no separators, uppercase."""
    if not mac:
        return ""
    clean = mac.replace(":", "").replace("-", "").upper()
    return clean[:6] if len(clean) >= 6 else ""


def is_randomized_mac(mac: str) -> bool:
    """Locally-administered bit set = randomized (privacy MAC)."""
    if not mac or len(mac) < 2:
        return False
    try:
        first_byte = int(mac[:2], 16)
        return bool(first_byte & 0x02)
    except ValueError:
        return False


def lookup_vendor(conn: sqlite3.Connection, mac: str) -> str | None:
    oui = mac_oui(mac)
    if not oui:
        return None
    row = conn.execute(
        "SELECT vendor FROM oui_lookup WHERE oui = ?", (oui,)
    ).fetchone()
    return row[0] if row else None


def iter_ndjson(path: Path) -> Iterator[dict[str, Any]]:
    """Yield each parseable JSON object from an NDJSON file."""
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                log.debug("Skipping malformed line %d in %s: %s", i, path.name, e)


# --- Enrichment of WiFi data from Kismet -------------------------------------
def process_kismet(
    conn: sqlite3.Connection, session_id: str, wifi_dir: Path
) -> dict[str, int]:
    """
    Kismet writes a SQLite database (.kismet) and a pcapng for every session.
    We pull the device summary from the .kismet file directly — it already
    has aggregated AP/client metadata, which is more useful than raw frames
    for a first pass.
    """
    counts = {"aps": 0, "clients": 0, "obs": 0}
    if not wifi_dir.exists():
        return counts

    kismet_dbs = list(wifi_dir.glob("*.kismet"))
    if not kismet_dbs:
        log.warning("No .kismet database in %s — has Kismet been run yet?", wifi_dir)
        return counts

    for kdb_path in kismet_dbs:
        log.info("Reading Kismet DB: %s", kdb_path.name)
        try:
            kdb = sqlite3.connect(f"file:{kdb_path}?mode=ro", uri=True)
            kdb.row_factory = sqlite3.Row
        except sqlite3.Error as e:
            log.error("Cannot open %s: %s", kdb_path, e)
            continue

        try:
            # Kismet's `devices` table has one row per unique device
            # with a JSON blob containing the full device record.
            for row in kdb.execute("SELECT devmac, type, device FROM devices"):
                try:
                    dev = json.loads(row["device"])
                except (json.JSONDecodeError, TypeError):
                    continue
                _ingest_kismet_device(
                    conn, session_id, row["devmac"], row["type"], dev, counts
                )
        except sqlite3.Error as e:
            log.warning("Error reading devices from %s: %s", kdb_path.name, e)
        finally:
            kdb.close()

    conn.commit()
    return counts


def _ingest_kismet_device(
    conn: sqlite3.Connection,
    session_id: str,
    mac: str,
    dtype: str,
    dev: dict[str, Any],
    counts: dict[str, int],
) -> None:
    mac = normalize_mac(mac)
    common = dev.get("kismet.device.base.commonname", "") or ""
    first_seen = _kismet_ts(dev.get("kismet.device.base.first_time"))
    last_seen = _kismet_ts(dev.get("kismet.device.base.last_time"))
    max_signal = _safe_int(
        dev.get("kismet.device.base.signal", {}).get("kismet.common.signal.max_signal")
    )
    channel = _safe_int(dev.get("kismet.device.base.channel"))
    freq = _safe_int(dev.get("kismet.device.base.frequency"))

    if dtype in ("Wi-Fi AP", "Wi-Fi Bridged"):
        ssid = (
            dev.get("dot11.device", {})
            .get("dot11.device.last_beaconed_ssid_record", {})
            .get("dot11.advertisedssid.ssid")
            or common
        )
        crypto = _kismet_crypto(dev)
        vendor = lookup_vendor(conn, mac) or dev.get(
            "kismet.device.base.manuf", ""
        )
        conn.execute(
            """
            INSERT INTO wifi_aps (bssid, last_ssid, vendor, encryption, channel,
                                  frequency_mhz, first_seen_utc, last_seen_utc,
                                  max_signal_dbm, obs_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(bssid) DO UPDATE SET
                last_ssid      = excluded.last_ssid,
                encryption     = excluded.encryption,
                channel        = excluded.channel,
                frequency_mhz  = excluded.frequency_mhz,
                last_seen_utc  = excluded.last_seen_utc,
                max_signal_dbm = MAX(IFNULL(wifi_aps.max_signal_dbm, -200),
                                     IFNULL(excluded.max_signal_dbm, -200)),
                obs_count      = wifi_aps.obs_count + 1
            """,
            (mac, ssid, vendor, crypto, channel, freq, first_seen,
             last_seen, max_signal),
        )
        counts["aps"] += 1

    elif dtype in ("Wi-Fi Client", "Wi-Fi Device"):
        probed = (
            dev.get("dot11.device", {})
            .get("dot11.device.probed_ssid_map", {})
        )
        probed_list = []
        if isinstance(probed, dict):
            for entry in probed.values():
                ssid = entry.get("dot11.probedssid.ssid")
                if ssid:
                    probed_list.append(ssid)
        vendor = lookup_vendor(conn, mac) or dev.get(
            "kismet.device.base.manuf", ""
        )
        conn.execute(
            """
            INSERT INTO wifi_clients (mac, vendor, is_randomized, probed_ssids,
                                      first_seen_utc, last_seen_utc, obs_count)
            VALUES (?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(mac) DO UPDATE SET
                last_seen_utc = excluded.last_seen_utc,
                probed_ssids  = excluded.probed_ssids,
                obs_count     = wifi_clients.obs_count + 1
            """,
            (
                mac,
                vendor,
                int(is_randomized_mac(mac)),
                json.dumps(sorted(set(probed_list))),
                first_seen,
                last_seen,
            ),
        )
        counts["clients"] += 1

    # Record an observation for the time-series view
    conn.execute(
        """
        INSERT INTO wifi_obs (session_id, timestamp_utc, obs_type, bssid,
                              client_mac, ssid, signal_dbm, channel, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            last_seen,
            dtype,
            mac if dtype.startswith("Wi-Fi AP") else None,
            mac if "Client" in dtype else None,
            common,
            max_signal,
            channel,
            json.dumps({"manuf": dev.get("kismet.device.base.manuf", "")}),
        ),
    )
    counts["obs"] += 1


def _kismet_ts(val: Any) -> str | None:
    if val is None:
        return None
    try:
        return datetime.fromtimestamp(int(val), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _kismet_crypto(dev: dict[str, Any]) -> str:
    crypto = (
        dev.get("dot11.device", {})
        .get("dot11.device.last_beaconed_ssid_record", {})
        .get("dot11.advertisedssid.crypt_set")
    )
    if not crypto:
        return ""
    # Kismet packs crypto as bitfield; for now, just store the raw int as text.
    return str(crypto)


def _safe_int(val: Any) -> int | None:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# --- Enrichment of RTL-SDR data from rtl_433 ---------------------------------
def process_rtl433(
    conn: sqlite3.Connection, session_id: str, sdr_dir: Path
) -> dict[str, int]:
    counts = {"devices": 0, "obs": 0}
    nd_path = sdr_dir / "rtl433.ndjson"
    if not nd_path.exists():
        return counts

    log.info("Processing rtl_433 NDJSON: %s", nd_path)
    seen_devices: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"first": None, "last": None, "count": 0,
                 "model": "", "id": "", "protocol": None, "freq": None}
    )

    for record in iter_ndjson(nd_path):
        # Skip stats records — they don't represent device sightings
        if "stats" in record:
            continue

        model = record.get("model", "unknown")
        dev_id = record.get("id", "")
        device_key = f"{model}:{dev_id}" if dev_id != "" else model
        ts = record.get("time", "")
        # rtl_433 with -M time:utc produces "YYYY-MM-DD HH:MM:SS"
        ts_iso = _normalize_rtl433_ts(ts)
        freq_mhz = record.get("freq", record.get("freq1"))
        rssi = record.get("rssi")
        snr = record.get("snr")
        protocol = record.get("protocol")

        info = seen_devices[device_key]
        info["model"] = model
        info["id"] = str(dev_id)
        info["protocol"] = protocol
        info["freq"] = freq_mhz
        if info["first"] is None or (ts_iso and ts_iso < info["first"]):
            info["first"] = ts_iso
        if info["last"] is None or (ts_iso and ts_iso > info["last"]):
            info["last"] = ts_iso
        info["count"] += 1

        conn.execute(
            """
            INSERT INTO rf_obs (session_id, timestamp_utc, device_key, model,
                                protocol, freq_mhz, rssi_db, snr_db, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, ts_iso, device_key, model, protocol,
             freq_mhz, rssi, snr, json.dumps(record)),
        )
        counts["obs"] += 1

    # Upsert all device summaries
    for device_key, info in seen_devices.items():
        conn.execute(
            """
            INSERT INTO rf_devices (device_key, model, device_id, protocol,
                                    typical_freq_mhz, first_seen_utc,
                                    last_seen_utc, obs_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_key) DO UPDATE SET
                last_seen_utc    = MAX(rf_devices.last_seen_utc,
                                       excluded.last_seen_utc),
                first_seen_utc   = MIN(rf_devices.first_seen_utc,
                                       excluded.first_seen_utc),
                typical_freq_mhz = excluded.typical_freq_mhz,
                obs_count        = rf_devices.obs_count + excluded.obs_count
            """,
            (
                device_key,
                info["model"],
                info["id"],
                info["protocol"],
                info["freq"],
                info["first"],
                info["last"],
                info["count"],
            ),
        )
        counts["devices"] += 1

    conn.commit()
    return counts


def _normalize_rtl433_ts(ts: str) -> str | None:
    if not ts:
        return None
    # "2024-01-15 12:34:56" → "2024-01-15T12:34:56+00:00"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return ts  # fall through, accept whatever rtl_433 gave us


# --- Session handling --------------------------------------------------------
def process_session(conn: sqlite3.Connection, session_dir: Path) -> None:
    manifest_path = session_dir / "manifest.json"
    if not manifest_path.exists():
        log.warning("No manifest in %s — skipping", session_dir)
        return

    manifest = json.loads(manifest_path.read_text())
    session_id = manifest["session_id"]
    log.info("=" * 60)
    log.info("Session: %s", session_id)

    ended_at = None
    end_file = session_dir / "ended_at_utc.txt"
    if end_file.exists():
        ended_at = end_file.read_text().strip()

    conn.execute(
        """
        INSERT INTO sessions (session_id, started_at_utc, ended_at_utc, hostname,
                              wifi_enabled, sdr_enabled, gps_enabled, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            ended_at_utc = excluded.ended_at_utc
        """,
        (
            session_id,
            manifest["started_at_utc"],
            ended_at,
            manifest.get("hostname"),
            int(manifest.get("collectors", {}).get("wifi", False)),
            int(manifest.get("collectors", {}).get("sdr", False)),
            int(manifest.get("collectors", {}).get("gps", False)),
            manifest.get("notes", ""),
        ),
    )

    wifi_counts = process_kismet(conn, session_id, session_dir / "wifi")
    sdr_counts = process_rtl433(conn, session_id, session_dir / "sdr")

    log.info("WiFi: %d APs, %d clients, %d obs",
             wifi_counts["aps"], wifi_counts["clients"], wifi_counts["obs"])
    log.info("RF:   %d devices, %d obs",
             sdr_counts["devices"], sdr_counts["obs"])
    conn.commit()


# --- Main --------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", nargs="?", help="Session directory to process")
    parser.add_argument("--all", action="store_true",
                        help="Process every session in capture/raw/")
    parser.add_argument("--db", default=None,
                        help="SQLite DB path (default: processing/wardrive.db)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    project_root = Path(__file__).resolve().parent.parent
    db_path = Path(args.db) if args.db else project_root / "processing" / "wardrive.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.all and not args.session:
        parser.error("Either provide a session directory or use --all")

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()

    if args.all:
        raw_root = project_root / "capture" / "raw"
        sessions = sorted(d for d in raw_root.iterdir() if d.is_dir())
        log.info("Found %d sessions to process", len(sessions))
        for s in sessions:
            process_session(conn, s)
    else:
        session_dir = Path(args.session).resolve()
        if not session_dir.is_dir():
            log.error("Not a directory: %s", session_dir)
            return 1
        process_session(conn, session_dir)

    log.info("=" * 60)
    log.info("Done. Database: %s", db_path)

    # Summary
    for table in ("sessions", "wifi_aps", "wifi_clients", "rf_devices"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        log.info("  %-15s %d rows", table, n)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
