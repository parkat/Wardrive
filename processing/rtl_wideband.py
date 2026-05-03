#!/usr/bin/env python3
"""
rtl_wideband.py — wideband spectrum scanner with peak lock-on

Workflow:
1. Scan 600-6000 MHz with rtl_power to find active frequencies
2. Identify peaks above threshold
3. Lock onto each peak with rtl_fm for a period
4. Log spectrum maps and recordings
5. Rescan and repeat
"""

import os
import sys
import json
import time
import subprocess
import tempfile
import signal
from datetime import datetime
from pathlib import Path


class WidebandScanner:
    def __init__(self, output_dir, start_mhz, end_mhz, step_mhz, scan_time,
                 lockup_time, peak_threshold):
        self.output_dir = Path(output_dir)
        self.start_mhz = start_mhz
        self.end_mhz = end_mhz
        self.step_mhz = step_mhz
        self.scan_time = scan_time
        self.lockup_time = lockup_time
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
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        csv_file = self.output_dir / f"scan_{timestamp}_n{scan_num}.csv"

        print(f"[wideband] Scan #{scan_num} ({self.start_mhz}-{self.end_mhz} MHz, "
              f"step {self.step_mhz} MHz)…", file=sys.stderr)

        try:
            cmd = [
                "rtl_power",
                "-f", f"{self.start_mhz}M:{self.end_mhz}M:{self.step_mhz}M",
                "-i", str(self.scan_time),
                "-1",  # single pass
                str(csv_file)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.scan_time * 3)

            if result.returncode != 0:
                print(f"[wideband] rtl_power failed: {result.stderr}", file=sys.stderr)
                return None

            if csv_file.exists():
                print(f"[wideband] Scan saved: {csv_file.name}", file=sys.stderr)
                return csv_file
        except subprocess.TimeoutExpired:
            print(f"[wideband] rtl_power timeout", file=sys.stderr)
        except Exception as e:
            print(f"[wideband] Error running rtl_power: {e}", file=sys.stderr)

        return None

    def parse_spectrum(self, csv_file):
        """Parse rtl_power CSV, return list of (freq_mhz, power_dbm) tuples sorted by power"""
        peaks = []
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
                        # rtl_power CSV: date, time, hz_low, hz_high, hz_step, samples, db...
                        # parts[2] is the low edge of the bin in Hz — convert to MHz
                        hz_low  = float(parts[2])
                        hz_high = float(parts[3])
                        freq_mhz = ((hz_low + hz_high) / 2.0) / 1e6
                        # rtl_power outputs multiple columns of power readings; take the mean
                        powers = [float(p) for p in parts[6:] if p.strip()]
                        avg_power = sum(powers) / len(powers) if powers else -100

                        if avg_power > self.peak_threshold:
                            peaks.append((freq_mhz, avg_power))
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"[wideband] Error parsing spectrum: {e}", file=sys.stderr)
            return []

        # Sort by power (strongest first)
        peaks.sort(key=lambda x: x[1], reverse=True)
        return peaks

    def record_frequency(self, freq_mhz, duration):
        """Lock onto a frequency and record with rtl_fm"""
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        freq_clean = f"{freq_mhz:.1f}".replace(".", "_")
        wav_file = self.output_dir / f"lockup_{timestamp}_{freq_clean}mhz.wav"

        print(f"[wideband] Locking {freq_mhz:.1f} MHz for {duration}s → {wav_file.name}",
              file=sys.stderr)

        try:
            cmd = [
                "rtl_fm",
                "-f", f"{int(freq_mhz * 1e6)}",  # frequency in Hz
                "-M", "wbfm",  # wideband FM (adjust if needed)
                "-s", "200000",  # sample rate
                "-g", "30",  # gain
                "-p", "0",  # ppm correction
            ]

            # Run for specified duration with timeout buffer
            with open(wav_file, "wb") as fout:
                proc = subprocess.Popen(cmd, stdout=fout, stderr=subprocess.PIPE)
                try:
                    proc.wait(timeout=duration)
                except subprocess.TimeoutExpired:
                    # Normal exit path — kill rtl_fm so it releases the SDR dongle
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

            if wav_file.exists() and wav_file.stat().st_size > 1000:
                print(f"[wideband] Recorded {wav_file.stat().st_size} bytes", file=sys.stderr)
                return True
        except Exception as e:
            print(f"[wideband] Error recording: {e}", file=sys.stderr)

        return False

    def log_peaks(self, scan_num, peaks):
        """Log discovered peaks to JSON"""
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
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
        """Main scan-lock-rescan loop"""
        scan_num = 0

        while self.running:
            scan_num += 1

            # Run spectrum scan
            csv_file = self.run_scan(scan_num)
            if not csv_file:
                print(f"[wideband] Scan failed, waiting before retry…", file=sys.stderr)
                time.sleep(5)
                continue

            # Parse peaks
            peaks = self.parse_spectrum(csv_file)
            self.log_peaks(scan_num, peaks)

            if not peaks:
                print(f"[wideband] No peaks above {self.peak_threshold} dBm threshold", file=sys.stderr)
                time.sleep(2)
                continue

            print(f"[wideband] Found {len(peaks)} peaks, top 5:", file=sys.stderr)
            for freq, power in peaks[:5]:
                print(f"[wideband]   {freq:8.1f} MHz @ {power:6.1f} dBm", file=sys.stderr)

            # Lock onto top 5 peaks
            for freq, power in peaks[:5]:
                if not self.running:
                    break
                print(f"[wideband] Recording peak #{freq}…", file=sys.stderr)
                self.record_frequency(freq, self.lockup_time)

            print(f"[wideband] Rescan in 5 seconds…", file=sys.stderr)
            time.sleep(5)


def main():
    if len(sys.argv) < 2:
        print("Usage: rtl_wideband.py <output_dir> [start_mhz] [end_mhz] [step_mhz] "
              "[scan_time] [lockup_time] [threshold_dbm]", file=sys.stderr)
        sys.exit(1)

    output_dir = sys.argv[1]
    start_mhz = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    end_mhz = int(sys.argv[3]) if len(sys.argv) > 3 else 6000
    step_mhz = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    scan_time = int(sys.argv[5]) if len(sys.argv) > 5 else 10
    lockup_time = int(sys.argv[6]) if len(sys.argv) > 6 else 30
    peak_threshold = int(sys.argv[7]) if len(sys.argv) > 7 else -40

    scanner = WidebandScanner(output_dir, start_mhz, end_mhz, step_mhz, scan_time,
                              lockup_time, peak_threshold)
    scanner.main_loop()


if __name__ == "__main__":
    main()
