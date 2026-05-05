#!/usr/bin/env python3
"""
rtl_wideband.py — wideband spectrum scanner

Passively scans 100-1700 MHz (R820T hardware range) with rtl_power and logs
all detected peaks to JSON + CSV for offline enrichment analysis. All signal
analysis is deferred to post-processing (processing/enrich_wideband.py) where
bandwidth, modulation, band classification, and signal statistics are computed.

Workflow:
1. Scan 100-1700 MHz with rtl_power
2. Identify and log peaks above threshold to JSON
3. Save raw spectrum CSV for later bandwidth/modulation analysis
4. Rescan continuously
"""

import math
import sys
import json
import time
import subprocess
import signal
from datetime import datetime, timezone
from pathlib import Path


class WidebandScanner:
    def __init__(self, output_dir, start_mhz, end_mhz, step_mhz, scan_time,
                 peak_threshold):
        self.output_dir = Path(output_dir)
        self.start_mhz = start_mhz
        self.end_mhz = end_mhz
        self.step_mhz = step_mhz
        self.scan_time = scan_time
        self.peak_threshold = peak_threshold
        self.running = True

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.setup_signal_handlers()

    def setup_signal_handlers(self):
        def handle_signal(signum, frame):
            print(f"[wideband] Received signal {signum}, shutting down gracefully…", file=sys.stderr)
            self.running = False
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    def run_scan(self, scan_num):
        """Run rtl_power to scan frequency range, return CSV path"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        csv_file = self.output_dir / f"scan_{timestamp}_n{scan_num}.csv"

        num_hops = max(1, math.ceil((self.end_mhz - self.start_mhz) / self.step_mhz))
        expected_secs = num_hops * self.scan_time + 30
        timeout = int(expected_secs * 1.5)

        print(f"[wideband] Scan #{scan_num} ({self.start_mhz}-{self.end_mhz} MHz, "
              f"step {self.step_mhz} MHz, ~{expected_secs}s)…", file=sys.stderr)

        try:
            cmd = [
                "rtl_power",
                "-f", f"{self.start_mhz}M:{self.end_mhz}M:{self.step_mhz}M",
                "-i", str(self.scan_time),
                "-1",  # single pass
                str(csv_file)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

            if result.returncode != 0:
                print(f"[wideband] rtl_power failed: {result.stderr}", file=sys.stderr)
                return None

            if csv_file.exists() and csv_file.stat().st_size > 0:
                print(f"[wideband] Scan saved: {csv_file.name}", file=sys.stderr)
                return csv_file
            print(f"[wideband] rtl_power produced no output", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[wideband] rtl_power timeout after {timeout}s", file=sys.stderr)
        except Exception as e:
            print(f"[wideband] Error running rtl_power: {e}", file=sys.stderr)

        return None

    def parse_spectrum(self, csv_file):
        """Parse rtl_power CSV, return deduplicated list of (freq_mhz, power_dbm) tuples.

        rtl_power CSV format per row:
          date, time, hz_low, hz_high, hz_step, n_samples, db0, db1, ...
        Each db column is one FFT bin at hz_low + i * hz_step.
        """
        all_bins = []
        try:
            with open(csv_file) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("Date"):
                        continue
                    parts = line.split(", ")
                    if len(parts) < 7:
                        continue
                    try:
                        hz_low  = float(parts[2])
                        hz_step = float(parts[4])
                        for i, raw in enumerate(parts[6:]):
                            raw = raw.strip()
                            if not raw:
                                continue
                            power = float(raw)
                            if power > self.peak_threshold:
                                freq_mhz = (hz_low + i * hz_step) / 1e6
                                all_bins.append((freq_mhz, power))
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"[wideband] Error parsing spectrum: {e}", file=sys.stderr)
            return []

        all_bins.sort(key=lambda x: x[1], reverse=True)
        return self._cluster_peaks(all_bins)

    def _cluster_peaks(self, bins, spacing_mhz=2.0):
        """Merge bins within spacing_mhz of each other, keeping the strongest per cluster."""
        clustered = []
        for freq, power in bins:
            for i, (cf, cp) in enumerate(clustered):
                if abs(freq - cf) <= spacing_mhz:
                    if power > cp:
                        clustered[i] = (freq, power)
                    break
            else:
                clustered.append((freq, power))
        clustered.sort(key=lambda x: x[1], reverse=True)
        return clustered

    def log_peaks(self, scan_num, peaks):
        """Log discovered peaks to JSON"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_file = self.output_dir / f"peaks_{timestamp}_n{scan_num}.json"

        peaks_data = {
            "scan_num": scan_num,
            "timestamp": timestamp,
            "peaks": [
                {"freq_mhz": freq, "power_dbm": power}
                for freq, power in peaks[:20]  # top 20
            ]
        }

        try:
            with open(log_file, "w") as f:
                json.dump(peaks_data, f, indent=2)
            print(f"[wideband] Peaks logged: {log_file.name}", file=sys.stderr)
        except Exception as e:
            print(f"[wideband] Error logging peaks: {e}", file=sys.stderr)

    def main_loop(self):
        """Main scan loop: acquire spectrum data for later enrichment"""
        scan_num = 0

        while self.running:
            scan_num += 1

            # Run spectrum scan
            csv_file = self.run_scan(scan_num)
            if not csv_file:
                print(f"[wideband] Scan failed, waiting before retry…", file=sys.stderr)
                time.sleep(5)
                continue

            # Parse peaks and log
            peaks = self.parse_spectrum(csv_file)
            self.log_peaks(scan_num, peaks)

            if not peaks:
                print(f"[wideband] No peaks above {self.peak_threshold} dBm threshold", file=sys.stderr)
                time.sleep(2)
                continue

            print(f"[wideband] Found {len(peaks)} peaks, top 10:", file=sys.stderr)
            for freq, power in peaks[:10]:
                print(f"[wideband]   {freq:8.1f} MHz @ {power:6.1f} dBm", file=sys.stderr)

            print(f"[wideband] Spectrum data saved. Analysis deferred to enrichment stage.", file=sys.stderr)
            print(f"[wideband] Rescan in 5 seconds…", file=sys.stderr)
            time.sleep(5)


def main():
    if len(sys.argv) < 2:
        print("Usage: rtl_wideband.py <output_dir> [start_mhz] [end_mhz] [step_mhz] "
              "[scan_time] [threshold_dbm]", file=sys.stderr)
        sys.exit(1)

    output_dir = sys.argv[1]
    start_mhz      = int(sys.argv[2]) if len(sys.argv) > 2 else 100   # R820T lower limit ~24 MHz
    end_mhz        = int(sys.argv[3]) if len(sys.argv) > 3 else 1700  # R820T upper limit ~1766 MHz
    step_mhz       = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    scan_time      = int(sys.argv[5]) if len(sys.argv) > 5 else 1     # seconds integration per step
    peak_threshold = int(sys.argv[6]) if len(sys.argv) > 6 else -40

    scanner = WidebandScanner(output_dir, start_mhz, end_mhz, step_mhz, scan_time,
                              peak_threshold)
    scanner.main_loop()


if __name__ == "__main__":
    main()
