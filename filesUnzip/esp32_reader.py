#!/usr/bin/env python3
"""
esp32_reader.py
Host-side serial reader for the ESP32 BLE scanner.

Reads NDJSON lines from the ESP32 over USB serial, injects a wall-clock
UTC timestamp ("ts") into each object, and writes to the session output
file.

The ESP32 firmware emits timestamps as millis-since-boot ("ms"). This
script replaces/supplements that with an accurate wall-clock timestamp so
downstream enrichment has reliable time data regardless of ESP32 clock
drift.

Usage:
    python3 esp32_reader.py --port /dev/ttyUSB0 --baud 921600 --output /path/to/esp32_ble.ndjson
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Try pyserial; it is listed in setup.sh as a dependency.
try:
    import serial
except ImportError:
    print("[esp32_reader] ERROR: pyserial not installed. Run: pip3 install pyserial --break-system-packages", file=sys.stderr)
    sys.exit(1)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run(port: str, baud: int, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[esp32_reader] Opening {port} @ {baud} baud", flush=True)
    print(f"[esp32_reader] Writing to {output_path}", flush=True)

    # Graceful shutdown on SIGTERM (sent by wardrive.sh cleanup trap)
    shutdown = False

    def _sigterm(signum, frame):
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    line_count = 0
    error_count = 0

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            timeout=1.0,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )
    except serial.SerialException as e:
        print(f"[esp32_reader] FATAL: Could not open {port}: {e}", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "a", buffering=1) as fout:  # line-buffered
        while not shutdown:
            try:
                raw = ser.readline()
            except serial.SerialException as e:
                print(f"[esp32_reader] Serial read error: {e}", file=sys.stderr)
                time.sleep(0.5)
                continue

            if not raw:
                continue  # timeout, try again

            try:
                line = raw.decode("utf-8", errors="replace").strip()
            except Exception:
                continue

            # Skip comment/banner lines emitted by firmware
            if not line or line.startswith("#"):
                continue

            # Inject UTC wall-clock timestamp
            try:
                obj = json.loads(line)
                obj["ts"] = utc_now_iso()
                out_line = json.dumps(obj, separators=(",", ":"))
                fout.write(out_line + "\n")
                line_count += 1
                if line_count % 1000 == 0:
                    print(f"[esp32_reader] {line_count} records written", flush=True)
            except json.JSONDecodeError:
                error_count += 1
                if error_count <= 10:
                    print(f"[esp32_reader] Non-JSON line (#{error_count}): {line[:80]}", file=sys.stderr)
                continue

    ser.close()
    print(f"[esp32_reader] Shutdown. {line_count} records written, {error_count} parse errors.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="ESP32 BLE serial reader")
    parser.add_argument("--port",   required=True, help="Serial port, e.g. /dev/ttyUSB0")
    parser.add_argument("--baud",   type=int, default=921600, help="Baud rate (default 921600)")
    parser.add_argument("--output", required=True, help="Output NDJSON file path")
    args = parser.parse_args()

    run(args.port, args.baud, Path(args.output))


if __name__ == "__main__":
    main()
