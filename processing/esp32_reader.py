#!/usr/bin/env python3
"""
esp32_reader.py
Host-side serial reader for the ESP32 BLE scanner.

Reads NDJSON lines from the ESP32 over USB serial, injects a wall-clock
UTC timestamp ("ts") and optionally GPS coordinates ("lat", "lon") into
each object, and writes to the session output file.

When --gpsd is passed, a background thread polls gpsd every second and
maintains the current fix. Every BLE record gets the most recent valid
lat/lon injected before it's written to disk. If gpsd has no fix, those
fields are omitted (not null — omitted) so the enricher knows to skip them.

Usage:
    python3 esp32_reader.py --port /dev/ttyUSB0 --baud 921600 \\
                            --output /path/to/esp32_ble.ndjson [--gpsd]
"""

import argparse
import json
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import serial
except ImportError:
    print(
        "[esp32_reader] ERROR: pyserial not installed. "
        "Run: pip3 install pyserial --break-system-packages",
        file=sys.stderr,
    )
    sys.exit(1)


# ── UTC timestamp ──────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ══════════════════════════════════════════════════════════════════════════════
# GPS position provider — polls gpsd in a background thread
# ══════════════════════════════════════════════════════════════════════════════
class GpsProvider:
    """
    Maintains a live fix from gpsd in a background thread.

    Thread-safe: lat/lon are read by the main thread for each BLE record.
    Connects to gpsd on localhost:2947. Reconnects automatically if gpsd
    drops the connection.

    Position is only reported when mode >= 2 (2D fix) AND satellite count
    meets the minimum threshold. If the fix is lost mid-drive, lat/lon are
    set back to None so records are written without coordinates rather than
    with stale ones.
    """

    GPSD_HOST = "127.0.0.1"
    GPSD_PORT = 2947
    MIN_SATS  = 4          # minimum satellites for a trustworthy fix
    POLL_HZ   = 1.0        # target polling rate (seconds between reads)

    def __init__(self):
        self._lat:  float | None = None
        self._lon:  float | None = None
        self._alt:  float | None = None
        self._sats: int          = 0
        self._mode: int          = 0   # 0=no data, 1=no fix, 2=2D, 3=3D
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="gpsd-reader")

    def start(self) -> None:
        self._thread.start()
        print("[gps_thread] Started — polling gpsd for position", flush=True)

    def stop(self) -> None:
        self._stop.set()

    def get_position(self) -> dict:
        """Return current position dict. Empty dict if no valid fix."""
        with self._lock:
            if (
                self._mode >= 2
                and self._lat is not None
                and self._lon is not None
                and self._sats >= self.MIN_SATS
            ):
                pos = {"lat": round(self._lat, 7), "lon": round(self._lon, 7)}
                if self._alt is not None:
                    pos["alt_m"] = round(self._alt, 1)
                pos["gps_sats"] = self._sats
                return pos
        return {}

    def has_fix(self) -> bool:
        with self._lock:
            return (
                self._mode >= 2
                and self._lat is not None
                and self._sats >= self.MIN_SATS
            )

    def _update(self, obj: dict) -> None:
        """Process a parsed gpsd JSON object and update internal state."""
        cls = obj.get("class")
        with self._lock:
            if cls == "TPV":
                self._mode = obj.get("mode", 0)
                if self._mode >= 2:
                    self._lat = obj.get("lat")
                    self._lon = obj.get("lon")
                    self._alt = obj.get("alt")
                else:
                    # Fix lost — clear position so we don't report stale data
                    self._lat = self._lon = self._alt = None
            elif cls == "SKY":
                # Try to get satellite count from detailed list first, fall back to summary field
                if "satellites" in obj:
                    self._sats = sum(
                        1 for sv in obj.get("satellites", []) if sv.get("used", False)
                    )
                else:
                    # Use summary field if detailed satellite list not available
                    self._sats = obj.get("uSat", 0)

    def _run(self) -> None:
        """Background thread: connect to gpsd, stream JSON, reconnect on drop."""
        backoff = 1
        buf = b""

        while not self._stop.is_set():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self.GPSD_HOST, self.GPSD_PORT))
                sock.settimeout(2.0)
                sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
                backoff = 1  # reset on successful connect
                buf = b""

                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break  # server closed connection
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            self._update(json.loads(line))
                        except (json.JSONDecodeError, KeyError):
                            pass

            except (ConnectionRefusedError, OSError) as e:
                print(
                    f"[gps_thread] gpsd connection error: {e} — retry in {backoff}s",
                    flush=True,
                )
                with self._lock:
                    # Clear position when gpsd is unreachable
                    self._lat = self._lon = self._alt = None
                    self._mode = 0
                    self._sats = 0
            finally:
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

            if not self._stop.is_set():
                self._stop.wait(timeout=backoff)
                backoff = min(backoff * 2, 30)


# ══════════════════════════════════════════════════════════════════════════════
# Main reader loop
# ══════════════════════════════════════════════════════════════════════════════

# Error-log throttling: emit first error immediately, then summarise
_ERR_INTERVAL = 30.0
_last_err_t   = 0.0
_suppressed   = 0


def _log_throttled(msg: str) -> None:
    global _last_err_t, _suppressed
    now = time.monotonic()
    if now - _last_err_t >= _ERR_INTERVAL:
        if _suppressed:
            print(f"[esp32_reader] (suppressed {_suppressed} similar errors)", flush=True)
            _suppressed = 0
        print(f"[esp32_reader] {msg}", flush=True)
        _last_err_t = now
    else:
        _suppressed += 1


def run(port: str, baud: int, output_path: Path, use_gpsd: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    gps: GpsProvider | None = None
    if use_gpsd:
        gps = GpsProvider()
        gps.start()
        # Give the GPS thread a moment to connect and get an initial fix
        print("[esp32_reader] Waiting up to 5s for initial GPS fix…", flush=True)
        for _ in range(10):
            if gps.has_fix():
                break
            time.sleep(0.5)
        if gps.has_fix():
            pos = gps.get_position()
            print(
                f"[esp32_reader] GPS fix: {pos.get('lat'):.6f}, {pos.get('lon'):.6f} "
                f"({pos.get('gps_sats')} sats)",
                flush=True,
            )
        else:
            print(
                "[esp32_reader] No GPS fix yet — coordinates will be added as fix arrives",
                flush=True,
            )

    print(f"[esp32_reader] Opening {port} @ {baud} baud", flush=True)
    print(f"[esp32_reader] Writing to {output_path}", flush=True)

    shutdown = False

    def _sigterm(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    line_count   = 0
    error_count  = 0
    gps_injected = 0
    reconnects   = 0
    backoff      = 1
    max_backoff  = 30

    # Append mode — reconnects don't truncate existing data
    with open(output_path, "a", buffering=1) as fout:
        while not shutdown:
            ser = None
            try:
                ser = serial.Serial(
                    port=port,
                    baudrate=baud,
                    timeout=1.0,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                )
                backoff = 1  # reset on successful open

                while not shutdown:
                    try:
                        raw = ser.readline()
                    except serial.SerialException as e:
                        _log_throttled(f"Serial read error: {e}")
                        break

                    if not raw:
                        continue  # read timeout, loop

                    try:
                        line = raw.decode("utf-8", errors="replace").strip()
                    except Exception:
                        continue

                    if not line or line.startswith("#"):
                        continue

                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        error_count += 1
                        if error_count <= 10:
                            print(
                                f"[esp32_reader] Non-JSON line (#{error_count}): {line[:80]}",
                                flush=True,
                            )
                        continue

                    # Inject wall-clock timestamp
                    obj["ts"] = utc_now_iso()

                    # Inject GPS coordinates if available
                    if gps is not None:
                        pos = gps.get_position()
                        if pos:
                            obj.update(pos)
                            gps_injected += 1

                    fout.write(json.dumps(obj, separators=(",", ":")) + "\n")
                    line_count += 1
                    if line_count % 1000 == 0:
                        gps_note = f", {gps_injected} with GPS coords" if gps else ""
                        print(
                            f"[esp32_reader] {line_count} records written{gps_note}",
                            flush=True,
                        )

            except serial.SerialException as e:
                _log_throttled(f"Cannot open {port}: {e}")
            except OSError as e:
                _log_throttled(f"OS error on {port}: {e}")
            finally:
                if ser is not None:
                    try:
                        ser.close()
                    except Exception:
                        pass

            if shutdown:
                break

            # Reconnect with backoff
            reconnects += 1
            _log_throttled(f"Reconnecting in {backoff}s (attempt {reconnects})…")
            for _ in range(backoff):
                if shutdown:
                    break
                time.sleep(1)
            backoff = min(backoff * 2, max_backoff)

    if gps is not None:
        gps.stop()

    gps_note = f", {gps_injected} with GPS coords" if gps else ""
    print(
        f"[esp32_reader] Shutdown. {line_count} records written{gps_note}, "
        f"{error_count} parse errors, {reconnects} reconnects.",
        flush=True,
    )


# ── Entrypoint ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ESP32 BLE serial reader with optional GPS")
    parser.add_argument("--port",   required=True,            help="Serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud",   type=int, default=921600, help="Baud rate (default 921600)")
    parser.add_argument("--output", required=True,            help="Output NDJSON file path")
    parser.add_argument("--gpsd",   action="store_true",
                        help="Connect to gpsd and inject lat/lon into each BLE record")
    args = parser.parse_args()

    run(args.port, args.baud, Path(args.output), use_gpsd=args.gpsd)


if __name__ == "__main__":
    main()
