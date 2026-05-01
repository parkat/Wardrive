#!/usr/bin/env python3
"""
enrich.py — Post-capture enrichment for the warDrive project.
Reads raw session data from capture/raw/<session>/ and populates
the wardrive SQLite database.
Collectors processed:
  • Kismet WiFi     → wifi_aps, wifi_clients, wifi_obs
  • rtl_433 SDR     → rf_devices, rf_obs
  • ESP32 BLE       → bt_devices, bt_obs
Online enrichment (results cached locally — each address/UUID looked up once):
  • macvendors.com  → OUI → manufacturer name from full IEEE registry (no key needed)
  • Wigle.net API   → BLE device sighting history + global count (API key required)
  • Bluetooth Numbers Database (GitHub) → service UUID + appearance → human names (no key)
To enable Wigle: add WIGLE_API_KEY=<base64 token> to config/wardrive.conf,
or export it as an environment variable. The token is shown on your Wigle
account page at wigle.net → "API Token" (base64 of "user:token").
Run with --offline to skip all network lookups (uses local data only).
Raw data is never modified. This script is safe to re-run.
Schema version: 1 (all additions are additive).
"""
import argparse
import json
import os
import sqlite3
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ── Circuit breakers (added by wardrive.sh fix-pass) ─────────────────────
class _CircuitBreaker:
    def __init__(self, label: str):
        self.label = label
        self.tripped = False
        self.reason = ""
    def trip(self, reason: str) -> None:
        if not self.tripped:
            print(f"  [{self.label}] disabled for rest of run: {reason}")
        self.tripped = True
        self.reason = reason
    def is_open(self) -> bool:
        return self.tripped

_macvendors_breaker = _CircuitBreaker("macvendors")
_wigle_breaker      = _CircuitBreaker("wigle")

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
    device_id       TEXT PRIMARY KEY,
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
    address                 TEXT PRIMARY KEY,
    address_type            TEXT,               -- public|random_static|random_resolvable|random_non_resolvable
    name                    TEXT,               -- last-seen advertised name
    is_randomized           INT DEFAULT 0,      -- locally-administered bit set in address
    manufacturer            TEXT,               -- from macvendors.com OUI lookup
    appearance              INT,                -- BLE appearance integer
    appearance_name         TEXT,               -- human label, e.g. "Watch" (from BT numbers DB)
    services                TEXT,               -- JSON array of UUIDs
    service_names           TEXT,               -- JSON array of human-readable service names
    apple_continuity_type   TEXT,               -- e.g. "AirPods", "Handoff", "NearbyAction"
    wigle_first_seen        TEXT,               -- earliest Wigle sighting timestamp
    wigle_last_seen         TEXT,               -- latest Wigle sighting timestamp
    wigle_ssid              TEXT,               -- device name as recorded by Wigle
    wigle_sighting_count    INT,                -- global sighting count in Wigle DB
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
    raw_payload     TEXT,           -- hex of full advertisement
    lat             REAL,           -- NULL until GPS is integrated
    lon             REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_bt_obs_session ON bt_obs(session_id);
CREATE INDEX IF NOT EXISTS idx_bt_obs_addr    ON bt_obs(address);
CREATE INDEX IF NOT EXISTS idx_bt_obs_time    ON bt_obs(timestamp_utc);
-- ── OUI lookup (shared across WiFi + BLE) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS oui_lookup (
    prefix          TEXT PRIMARY KEY,
    organization    TEXT
);
-- ── Online enrichment cache ───────────────────────────────────────────────────
-- Persists across all runs. Each (source, key) is fetched at most once.
-- result is JSON-encoded; NULL means "looked up, got nothing" (explicit miss).
-- fetched_at allows future cache expiry if needed.
CREATE TABLE IF NOT EXISTS enrichment_cache (
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    result      TEXT,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (source, key)
);
"""

# ── BLE address-type decoder ───────────────────────────────────────────────────
def decode_address_type(addr: str, addr_type_int: int) -> str:
    if addr_type_int == 0:
        return "public"
    try:
        high_byte = int(addr.split(":")[0], 16)
        top_bits = (high_byte >> 6) & 0x03
        if top_bits == 0b11:  return "random_static"
        elif top_bits == 0b01: return "random_resolvable"
        else:                  return "random_non_resolvable"
    except (ValueError, IndexError):
        return "random"

# ── Local fallback manufacturer table ─────────────────────────────────────────
KNOWN_MFG = {
    0x004C: "Apple",       0x0006: "Microsoft",    0x0075: "Samsung",
    0x00E0: "Google",      0x0499: "Ruuvi",         0x0059: "Nordic Semiconductor",
    0x0157: "Garmin",      0x01D8: "Tile",           0x0171: "Amazon",
    0x0397: "Bose",        0x0310: "Jabra",          0x0089: "Plantronics",
    0x038F: "Sony",        0x004F: "Beats",           0x03DA: "Nothing",
}

# ══════════════════════════════════════════════════════════════════════════════
# Cache helpers
# ════════════════════════════════════════════════════════════════════════════════
def _cache_get(db: sqlite3.Connection, source: str, key: str):
    """Return (hit, result). hit=False means not in cache. result may be None."""
    row = db.execute(
        "SELECT result FROM enrichment_cache WHERE source=? AND key=?",
        (source, key)
    ).fetchone()
    if row is None:
        return False, None
    return True, (json.loads(row[0]) if row[0] is not None else None)

def _cache_set(db: sqlite3.Connection, source: str, key: str, result):
    db.execute(
        """INSERT OR REPLACE INTO enrichment_cache (source, key, result, fetched_at)
           VALUES (?, ?, ?, ?)""",
        (source, key,
         json.dumps(result) if result is not None else None,
         datetime.now(timezone.utc).isoformat())
    )
    db.commit()

def _http_get_json(url: str, headers: dict | None = None, timeout: int = 8,
                   label: str = "", breaker: "_CircuitBreaker | None" = None):
    """GET url, return parsed JSON or None on any failure. Logs errors.
    If `breaker` is provided, trips it on HTTP 401 (auth required) or
    HTTP 429 (rate limited) so callers stop trying for the rest of the run.
    """
    if breaker is not None and breaker.is_open():
        return None
    req = urllib.request.Request(url, headers=headers or {})
    req.add_header("User-Agent", "warDrive-enrichment/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        tag = f"[{label}] " if label else ""
        print(f"  {tag}HTTP {e.code} from {url.split('?')[0]}")
        if breaker is not None and e.code in (401, 429):
            reasons = {401: "auth required (HTTP 401)",
                       429: "rate limited (HTTP 429)"}
            breaker.trip(reasons[e.code])
        return None
    except urllib.error.URLError as e:
        tag = f"[{label}] " if label else ""
        print(f"  {tag}Network error: {e.reason}")
        return None
    except Exception as e:
        tag = f"[{label}] " if label else ""
        print(f"  {tag}Unexpected error: {e}")
        return None

# ═════════════════════════════════════════════════════════════════════════════
# macvendors.com — OUI → manufacturer
# ═════════════════════════════════════════════════════════════════════════════
# Free tier: ~1 req/s, 1000 req/day. No API key required.
# We look up by OUI (first 3 octets) so one lookup covers all devices from
# the same manufacturer.
_macvendors_last: float = 0.0

def lookup_oui_online(db: sqlite3.Connection, mac: str) -> str | None:
    global _macvendors_last
    oui = mac.replace(":", "")[:6].upper()
    hit, cached = _cache_get(db, "macvendors", oui)
    if hit:
        return cached
    # Skip the API entirely if the breaker is tripped (401/429 earlier)
    if _macvendors_breaker.is_open():
        return None
    elapsed = time.time() - _macvendors_last
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    _macvendors_last = time.time()
    # JSON endpoint returns {"data": {"organization_name": "..."}, ...}
    data = _http_get_json(
        f"https://api.macvendors.com/v1/lookup/{urllib.parse.quote(oui)}",
        label="macvendors", breaker=_macvendors_breaker)
    result = None
    if isinstance(data, dict):
        result = (data.get("data") or {}).get("organization_name")
    # Fallback: plain-text endpoint (skip if breaker tripped during JSON call)
    if not result and not _macvendors_breaker.is_open():
        req = urllib.request.Request(
            f"https://api.macvendors.com/{urllib.parse.quote(oui)}",
            headers={"User-Agent": "warDrive-enrichment/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode().strip()
                if text and "Not Found" not in text and len(text) < 120:
                    result = text
        except urllib.error.HTTPError as e:
            print(f"  [macvendors] HTTP {e.code} for OUI {oui}")
            if e.code in (401, 429):
                reasons = {401: "auth required (HTTP 401)",
                           429: "rate limited (HTTP 429)"}
                _macvendors_breaker.trip(reasons[e.code])
        except Exception as e:
            print(f"  [macvendors] Error for OUI {oui}: {e}")
    # Only cache definitive results — don't cache misses caused by the
    # breaker tripping mid-lookup, so a future run can retry.
    if result is not None or not _macvendors_breaker.is_open():
        _cache_set(db, "macvendors", oui, result)
    return result

def get_manufacturer(db: sqlite3.Connection, mac: str, mfg_id: int | None,
                     verbose: bool = False) -> str | None:
    """Manufacturer with fallback: local table → macvendors OUI lookup."""
    if mfg_id is not None and mfg_id in KNOWN_MFG:
        name = KNOWN_MFG[mfg_id]
        if verbose:
            print(f"    manufacturer: {name} (local table, mfg_id=0x{mfg_id:04X})")
        return name
    # Don't look up locally-administered (randomized) MACs — OUI is meaningless
    try:
        high_byte = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return None
    if high_byte & 0x02:
        if verbose:
            print(f"    manufacturer: skipped (randomized MAC)")
        return None
    name = lookup_oui_online(db, mac)
    if verbose:
        if name:
            print(f"    manufacturer: {name} (macvendors OUI lookup)")
        else:
            print(f"    manufacturer: not found (macvendors miss)")
    return name

# ═════════════════════════════════════════════════════════════════════════════
# Bluetooth Numbers Database — UUID + appearance names
# ═════════════════════════════════════════════════════════════════════════════
# Source: github.com/NordicSemiconductor/bluetooth-numbers-database
# Entire table fetched once and cached as a single entry.
_BT_NUMBERS_BASE = (
    "https://raw.githubusercontent.com/NordicSemiconductor/"
    "bluetooth-numbers-database/master/v1"
)

def _fetch_bt_numbers_table(db: sqlite3.Connection, table: str) -> dict:
    hit, cached = _cache_get(db, f"bt_numbers_{table}", "__all__")
    if hit and cached:
        return cached
    # gap_appearance has a different filename than the other tables
    filename = "gap_appearance" if table == "appearance_values" else table
    data = _http_get_json(
        f"{_BT_NUMBERS_BASE}/{filename}.json",
        label=f"bt_numbers/{filename}")
    if not data or not isinstance(data, list):
        _cache_set(db, f"bt_numbers_{table}", "__all__", {})
        return {}
    mapping = {}
    if table == "appearance_values":
        # gap_appearance schema: each entry has "category" (int) and optional
        # "subcategory" list of {"value": int, "name": str}.
        # BLE appearance value = (category << 6) | subcategory_value
        # category-only entries (subcategory_value=0) map to the category name.
        for entry in data:
            cat  = entry.get("category", 0)
            name = entry.get("name", "")
            # category-only appearance value (subcategory bits = 0)
            mapping[cat << 6] = name
            for sub in entry.get("subcategory", []):
                val      = (cat << 6) | (sub.get("value", 0) & 0x3F)
                sub_name = f"{name} – {sub.get('name', '')}"
                mapping[val] = sub_name
    else:
        # service_uuids / characteristic_uuids etc.
        # Each entry: {"identifier": "0x180F", "name": "Battery Service", ...}
        for entry in data:
            ident = entry.get("identifier") or entry.get("uuid") or ""
            name  = entry.get("name", "")
            if ident and name:
                key = ident.upper().lstrip("0X").lstrip("0") or "0"
                mapping[key] = name
    _cache_set(db, f"bt_numbers_{table}", "__all__", mapping)
    return mapping

def resolve_service_names(db: sqlite3.Connection, uuids: list) -> list:
    if not uuids:
        return []
    table = _fetch_bt_numbers_table(db, "service_uuids")
    names = []
    for uuid in uuids:
        clean = uuid.replace("-", "").upper()
        short = clean[:4].lstrip("0") or "0"
        name  = table.get(short) or table.get(clean)
        names.append(name if name else uuid)
    return names

def resolve_appearance_name(db: sqlite3.Connection, val: int) -> str | None:
    table = _fetch_bt_numbers_table(db, "appearance_values")
    # Try exact match first, then category-only (subcategory bits masked off)
    return table.get(val) or table.get((val >> 6) << 6)

# ═════════════════════════════════════════════════════════════════════════════
# Wigle.net — BLE device sighting history
# ═════════════════════════════════════════════════════════════════════════════
# Free account: ~10 req/min.
# API key = base64("username:apiToken") from wigle.net account page.
# Only public (non-randomized) MACs are worth looking up.
_wigle_last: float = 0.0

def lookup_wigle_ble(db: sqlite3.Connection, mac: str, api_key: str,
                     verbose: bool = False) -> dict | None:
    global _wigle_last
    # Skip API entirely if breaker tripped earlier this run (401/429)
    if _wigle_breaker.is_open():
        if verbose:
            print(f"    wigle: skipped ({_wigle_breaker.reason})")
        return None
    # Skip randomized MACs — they rotate and won't be in Wigle meaningfully
    try:
        high_byte = int(mac.split(":")[0], 16)
    except (ValueError, IndexError):
        return None
    if high_byte & 0x02:
        if verbose:
            print(f"    wigle: skipped (randomized MAC)")
        return None
    hit, cached = _cache_get(db, "wigle_ble", mac)
    if hit:
        if verbose:
            if cached:
                print(f"    wigle: cached — {cached['wigle_sighting_count']} global sightings")
            else:
                print(f"    wigle: not found (cached)")
        return cached
    elapsed = time.time() - _wigle_last
    if elapsed < 6.5:
        time.sleep(6.5 - elapsed)
    _wigle_last = time.time()
    if verbose:
        print(f"    wigle: querying API…")
    url = (
        "https://api.wigle.net/api/v2/bluetooth/search"
        f"?netid={urllib.parse.quote(mac)}&first=0&resultsPerPage=1"
    )
    data = _http_get_json(url, headers={"Authorization": f"Basic {api_key}"},
                          label="wigle", breaker=_wigle_breaker)
    result = None
    if data and data.get("success") and data.get("results"):
        r = data["results"][0]
        result = {
            "wigle_first_seen":     r.get("firsttime"),
            "wigle_last_seen":      r.get("lasttime"),
            "wigle_ssid":           r.get("ssid"),
            "wigle_sighting_count": data.get("totalResults"),
        }
        if verbose:
            print(f"    wigle: found — {result['wigle_sighting_count']} global sightings")
    else:
        if verbose:
            print(f"    wigle: not in database")
    # Don't cache a "not found" caused by the breaker tripping mid-lookup
    if result is not None or not _wigle_breaker.is_open():
        _cache_set(db, "wigle_ble", mac, result)
    return result

# ═════════════════════════════════════════════════════════════════════════════
# BLE session processor
# ═════════════════════════════════════════════════════════════════════════════
def process_esp32_ble(
    db: sqlite3.Connection,
    session_id: str,
    bt_ndjson: Path,
    wigle_api_key: str | None = None,
    online: bool = True,
    verbose: bool = False,
) -> int:
    if not bt_ndjson.exists():
        print(f"  [ble] No BLE data file at {bt_ndjson} — skipping")
        return 0
    obs_count    = 0
    device_cache: dict = {}   # addr → aggregated dict
    # ── Pass 1: read NDJSON, insert observations, aggregate per-device ──────
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
            ts = rec.get("ts")
            if ts is None or isinstance(ts, (int, float)):
                continue
            rssi       = rec.get("rssi")
            raw_hex    = rec.get("raw")
            addr_type  = rec.get("addr_type", 1)
            is_rand    = rec.get("rand", 0)
            name       = rec.get("name")
            mfg_id     = rec.get("mfg_id")
            appearance = rec.get("appearance")
            services   = rec.get("services")
            apple_type = rec.get("apple_type")
            db.execute(
                """INSERT INTO bt_obs
                       (session_id, timestamp_utc, address, rssi_dbm, raw_payload)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, ts, addr, rssi, raw_hex),
            )
            obs_count += 1
            if addr not in device_cache:
                device_cache[addr] = {
                    "address_type": decode_address_type(addr, addr_type),
                    "is_randomized": is_rand,
                    "name":       name,
                    "mfg_id":     mfg_id,
                    "appearance": appearance,
                    "services":   set(services) if services else set(),
                    "apple_type": apple_type,
                    "first_seen": ts,
                    "last_seen":  ts,
                    "max_rssi":   rssi if rssi is not None else -999,
                    "obs_count":  1,
                }
            else:
                d = device_cache[addr]
                if name       and not d["name"]:       d["name"]       = name
                if apple_type and not d["apple_type"]: d["apple_type"] = apple_type
                if appearance is not None and d["appearance"] is None:
                    d["appearance"] = appearance
                if services: d["services"].update(services)
                if rssi is not None and rssi > d["max_rssi"]: d["max_rssi"] = rssi
                if ts < d["first_seen"]: d["first_seen"] = ts
                if ts > d["last_seen"]:  d["last_seen"]  = ts
                if mfg_id is not None and d["mfg_id"] is None: d["mfg_id"] = mfg_id
                d["obs_count"] += 1
    db.commit()
    unique = len(device_cache)
    print(f"  [ble] {obs_count} observations, {unique} unique devices")
    # ── Pass 2: enrich and upsert devices ─────────────────────────────────
    if online and unique > 0:
        print(f"  [ble] Fetching Bluetooth numbers DB (service UUIDs + appearance)…")
        _fetch_bt_numbers_table(db, "service_uuids")
        _fetch_bt_numbers_table(db, "appearance_values")
        oui_hits = wigle_hits = 0
        for i, (addr, d) in enumerate(device_cache.items(), 1):
            if verbose:
                print(f"  [ble] {i}/{unique}: {addr}  "
                      f"(type={d['address_type']}, rssi={d['max_rssi']}, obs={d['obs_count']})")
            else:
                print(f"  [ble] Enriching {i}/{unique}: {addr}   ", end="\r", flush=True)
            manufacturer    = get_manufacturer(db, addr, d["mfg_id"], verbose=verbose)
            if manufacturer: oui_hits += 1
            services_list   = sorted(d["services"])
            service_names   = resolve_service_names(db, services_list)
            appearance_name = resolve_appearance_name(db, d["appearance"]) \
                              if d["appearance"] is not None else None
            if verbose and appearance_name:
                print(f"    appearance: {appearance_name} ({d['appearance']})")
            wigle = None
            if wigle_api_key:
                wigle = lookup_wigle_ble(db, addr, wigle_api_key, verbose=verbose)
                if wigle: wigle_hits += 1
            db.execute(
                """INSERT INTO bt_devices
                       (address, address_type, name, is_randomized, manufacturer,
                        appearance, appearance_name, services, service_names,
                        apple_continuity_type,
                        wigle_first_seen, wigle_last_seen, wigle_ssid, wigle_sighting_count,
                        first_seen_utc, last_seen_utc, max_rssi_dbm, obs_count)
                   VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?)
                   ON CONFLICT(address) DO UPDATE SET
                       name                  = COALESCE(excluded.name, bt_devices.name),
                       manufacturer          = COALESCE(excluded.manufacturer, bt_devices.manufacturer),
                       appearance            = COALESCE(excluded.appearance, bt_devices.appearance),
                       appearance_name       = COALESCE(excluded.appearance_name, bt_devices.appearance_name),
                       services              = COALESCE(excluded.services, bt_devices.services),
                       service_names         = COALESCE(excluded.service_names, bt_devices.service_names),
                       apple_continuity_type = COALESCE(excluded.apple_continuity_type,
                                                         bt_devices.apple_continuity_type),
                       wigle_first_seen      = COALESCE(excluded.wigle_first_seen, bt_devices.wigle_first_seen),
                       wigle_last_seen       = COALESCE(excluded.wigle_last_seen, bt_devices.wigle_last_seen),
                       wigle_ssid            = COALESCE(excluded.wigle_ssid, bt_devices.wigle_ssid),
                       wigle_sighting_count  = COALESCE(excluded.wigle_sighting_count,
                                                         bt_devices.wigle_sighting_count),
                       last_seen_utc         = MAX(bt_devices.last_seen_utc, excluded.last_seen_utc),
                       first_seen_utc        = MIN(bt_devices.first_seen_utc, excluded.first_seen_utc),
                       max_rssi_dbm          = MAX(bt_devices.max_rssi_dbm, excluded.max_rssi_dbm),
                       obs_count             = bt_devices.obs_count + excluded.obs_count
                """,
                (
                    addr, d["address_type"], d["name"], d["is_randomized"], manufacturer,
                    d["appearance"], appearance_name,
                    json.dumps(services_list) if services_list else None,
                    json.dumps(service_names) if service_names else None,
                    d["apple_type"],
                    wigle.get("wigle_first_seen")     if wigle else None,
                    wigle.get("wigle_last_seen")      if wigle else None,
                    wigle.get("wigle_ssid")           if wigle else None,
                    wigle.get("wigle_sighting_count") if wigle else None,
                    d["first_seen"], d["last_seen"],
                    d["max_rssi"] if d["max_rssi"] > -999 else None,
                    d["obs_count"],
                ),
            )
        db.commit()
        print()  # clear \r line
        print(f"  [ble] OUI resolved: {oui_hits}/{unique}  "
              f"Wigle hits: {wigle_hits}/{unique}")
    else:
        # Offline: upsert with local data only
        for addr, d in device_cache.items():
            manufacturer  = KNOWN_MFG.get(d["mfg_id"]) if d["mfg_id"] else None
            services_json = json.dumps(sorted(d["services"])) if d["services"] else None
            db.execute(
                """INSERT INTO bt_devices
                       (address, address_type, name, is_randomized, manufacturer,
                        appearance, services, apple_continuity_type,
                        first_seen_utc, last_seen_utc, max_rssi_dbm, obs_count)
                   VALUES (?,?,?,?,?, ?,?,?, ?,?,?,?)
                   ON CONFLICT(address) DO UPDATE SET
                       name          = COALESCE(excluded.name, bt_devices.name),
                       manufacturer  = COALESCE(excluded.manufacturer, bt_devices.manufacturer),
                       last_seen_utc = MAX(bt_devices.last_seen_utc, excluded.last_seen_utc),
                       first_seen_utc= MIN(bt_devices.first_seen_utc, excluded.first_seen_utc),
                       max_rssi_dbm  = MAX(bt_devices.max_rssi_dbm, excluded.max_rssi_dbm),
                       obs_count     = bt_devices.obs_count + excluded.obs_count
                """,
                (addr, d["address_type"], d["name"], d["is_randomized"], manufacturer,
                 d["appearance"], services_json, d["apple_type"],
                 d["first_seen"], d["last_seen"],
                 d["max_rssi"] if d["max_rssi"] > -999 else None, d["obs_count"]),
            )
        db.commit()
    return obs_count

# ── Process WiFi (Kismet SQLite DB) ───────────────────────────────────
def process_kismet(db: sqlite3.Connection, session_id: str, wifi_dir: Path) -> int:
    kismet_files = list(wifi_dir.glob("Kismet-*.kismet"))
    if not kismet_files:
        print("  [wifi] No Kismet DB files found — skipping")
        return 0
    
    print(f"  [wifi] Found {len(kismet_files)} .kismet SQLite file(s)...")
    obs_count = 0

    for kf in kismet_files:
        try:
            # Connect to the Kismet capture DB as a read-only client
            with sqlite3.connect(f"file:{kf}?mode=ro", uri=True) as kdb:
                kdb.row_factory = sqlite3.Row
                
                # 1️⃣ Access Points (device_type=1 in Kismet)
                for ap in kdb.execute("""
                    SELECT bssid, ssid, encryption, channel, max_signal, first_seen, last_seen, num_pkts
                    FROM devices WHERE device_type=1
                """):
                    db.execute("""
                        INSERT OR REPLACE INTO wifi_aps 
                        (bssid, ssid, encryption, channel, max_signal_dbm, first_seen_utc, last_seen_utc, obs_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ap["bssid"], 
                        ap["ssid"], 
                        ap["encryption"], 
                        ap["channel"], 
                        ap["max_signal"], 
                        ap["first_seen"], 
                        ap["last_seen"], 
                        ap["num_pkts"]
                    ))

                # 2️⃣ WiFi Clients (device_type=2 in Kismet)
                for cli in kdb.execute("""
                    SELECT bssid, ssid, first_seen, last_seen, num_pkts
                    FROM devices WHERE device_type=2
                """):
                    db.execute("""
                        INSERT OR REPLACE INTO wifi_clients 
                        (mac, probe_ssid, first_seen_utc, last_seen_utc, obs_count)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        cli["bssid"], 
                        cli["ssid"] or "PROBE_REQUEST", 
                        cli["first_seen"], 
                        cli["last_seen"], 
                        cli["num_pkts"]
                    ))

                # 3️⃣ WiFi Observations (packets table)
                # Kismet's packets table is large. We cap at 50k per run to avoid blocking.
                for obs in kdb.execute("""
                    SELECT time, bssid, signal
                    FROM packets LIMIT 50000
                """):
                    db.execute("""
                        INSERT INTO wifi_obs (session_id, timestamp_utc, bssid, signal_dbm)
                        VALUES (?, ?, ?, ?)
                    """, (
                        session_id, 
                        obs["time"], 
                        obs["bssid"], 
                        obs["signal"]
                    ))
                    obs_count += 1
                    
            db.commit()
            print(f"  [wifi] ✅ {kf.name} parsed successfully")
            
        except sqlite3.DatabaseError as e:
            print(f"  [wifi] ⚠️ Corrupted or locked Kismet DB: {kf.name} ({e})")
        except Exception as e:
            print(f"  [wifi] ⚠️ Unexpected error parsing {kf.name}: {e}")

    print(f"  [wifi] Done. ~{obs_count} observations + AP/Client stats written.")
    return obs_count

# ── Process SDR (rtl_433) ──────────────────────────────────────────────────────
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
                ts         = rec.get("time") or rec.get("timestamp_utc")
                model      = rec.get("model")
                freq       = rec.get("freq")
                protocol   = rec.get("protocol")
                device_key = f"{model}_{rec.get('id', 'unknown')}"
                db.execute(
                    """INSERT OR IGNORE INTO rf_devices
                           (device_id, model, protocol, frequency_mhz,
                            first_seen_utc, last_seen_utc, obs_count)
                       VALUES (?,?,?,?,?,?,0)""",
                    (device_key, model, str(protocol) if protocol else None, freq, ts, ts),
                )
                db.execute(
                    "UPDATE rf_devices SET last_seen_utc=?,obs_count=obs_count+1 WHERE device_id=?",
                    (ts, device_key),
                )
                db.execute(
                    "INSERT INTO rf_obs (session_id,timestamp_utc,device_id,raw_json) VALUES(?,?,?,?)",
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
           VALUES (?,?,?,?,?,?,?,0)""",
        (session_id, manifest.get("started_at_utc"), manifest.get("ended_at_utc"),
         manifest.get("hostname"),
         1 if collectors.get("wifi") else 0,
         1 if collectors.get("sdr") else 0,
         1 if collectors.get("esp32") else 0),
    )
    db.commit()
    return session_id

# ── Wigle key loader ───────────────────────────────────────────────────────────
def load_wigle_key(conf_path: Path) -> str | None:
    key = os.environ.get("WIGLE_API_KEY", "").strip()
    if key:
        return key
    if conf_path.exists():
        with open(conf_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("WIGLE_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return None

# ── Main ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="warDrive enrichment pipeline")
    parser.add_argument("--raw-dir", default=str(Path(__file__).parent.parent / "capture" / "raw"))
    parser.add_argument("--db",      default=str(Path(__file__).parent / "wardrive.db"))
    parser.add_argument("--session", default=None, help="Process a single named session")
    parser.add_argument("--all",     action="store_true", help="Re-process already-seen sessions")
    parser.add_argument("--offline", action="store_true", help="Skip all network lookups")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show per-device enrichment detail (source of each lookup, wigle outcome)")
    args = parser.parse_args()
    raw_dir   = Path(args.raw_dir)
    db_path   = Path(args.db)
    conf_path = Path(__file__).parent.parent / "config" / "wardrive.conf"
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA_SQL)
    wigle_key = None
    if not args.offline:
        wigle_key = load_wigle_key(conf_path)
        if wigle_key:
            print("[enrich] Wigle API key found ✓")
        else:
            print("[enrich] No Wigle key — add WIGLE_API_KEY to wardrive.conf to enable")
        print("[enrich] Online enrichment: ON  (--offline to disable)")
    else:
        print("[enrich] Online enrichment: OFF")
    totals = {"sdr": 0, "ble_obs": 0, "wifi_obs": 0}
    session_dirs = (
        [raw_dir / args.session] if args.session
        else (sorted(raw_dir.iterdir()) if raw_dir.exists() else [])
    )
    for session_dir in session_dirs:
        if not session_dir.is_dir():
            continue
        print(f"\n[enrich] Session: {session_dir.name}")
        session_id = load_session(db, session_dir)
        if not session_id:
            continue
        if not args.all:
            existing  = db.execute("SELECT COUNT(*) FROM bt_obs WHERE session_id=?",
                                   (session_id,)).fetchone()[0]
            existing += db.execute("SELECT COUNT(*) FROM rf_obs WHERE session_id=?",
                                   (session_id,)).fetchone()[0]
            existing += db.execute("SELECT COUNT(*) FROM wifi_obs WHERE session_id=?",
                                   (session_id,)).fetchone()[0]
            if existing > 0:
                print(f"  Already processed ({existing} records) — use --all to re-run")
                continue
        wifi_obs = process_kismet(db, session_id, session_dir / "wifi")
        totals["wifi_obs"] += wifi_obs
        totals["sdr"]     += process_rtl433(db, session_id, session_dir / "sdr")
        totals["ble_obs"] += process_esp32_ble(
            db, session_id,
            session_dir / "bt" / "esp32_ble.ndjson",
            wigle_api_key=wigle_key,
            online=not args.offline,
            verbose=args.verbose,
        )
    # ── Summary ───────────────────────────────────────────────────────────────────
    q = lambda sql: db.execute(sql).fetchone()[0]
    print("\n" + "=" * 60)
    print(f"  wardrive.db:           {db_path}")
    print(f"  WiFi obs:              {totals['wifi_obs']}")
    print(f"  RF obs:                {totals['sdr']}")
    print(f"  BLE obs:               {totals['ble_obs']}")
    print(f"  Unique BLE devices:    {q('SELECT COUNT(*) FROM bt_devices')}")
    print(f"  With manufacturer:     {q('SELECT COUNT(*) FROM bt_devices WHERE manufacturer IS NOT NULL')}")
    print(f"  With service names:    {q('SELECT COUNT(*) FROM bt_devices WHERE service_names IS NOT NULL')}")
    print(f"  Apple Continuity:      {q('SELECT COUNT(*) FROM bt_devices WHERE apple_continuity_type IS NOT NULL')}")
    print(f"  Found in Wigle:        {q('SELECT COUNT(*) FROM bt_devices WHERE wigle_sighting_count IS NOT NULL')}")
    print(f"  Public (stable) MAC:   {q('SELECT COUNT(*) FROM bt_devices WHERE is_randomized=0')}")
    print(f"  Enrichment cache:      {q('SELECT COUNT(*) FROM enrichment_cache')} entries")
    print("=" * 60)
    db.close()

if __name__ == "__main__":
    main()
