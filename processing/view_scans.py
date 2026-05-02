#!/usr/bin/env python3
"""
view_scans.py — visualize wideband SDR spectrum scans

Usage:
    python3 view_scans.py <scan_csv_file>

Prints a simple ASCII frequency/power chart of the spectrum data.
"""

import sys
import json
from pathlib import Path


def view_spectrum_csv(csv_file):
    """Display spectrum scan data in ASCII format"""
    frequencies = {}

    with open(csv_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("Date"):
                continue
            parts = line.split(", ")
            if len(parts) < 7:
                continue
            try:
                freq_mhz = float(parts[2])
                powers = [float(p) for p in parts[6:] if p.strip()]
                avg_power = sum(powers) / len(powers) if powers else -100
                frequencies[freq_mhz] = avg_power
            except (ValueError, IndexError):
                continue

    if not frequencies:
        print("No data found")
        return

    # Sort by frequency
    sorted_freqs = sorted(frequencies.items())

    # Find min/max power for scaling
    powers = [p for _, p in sorted_freqs]
    min_power = min(powers)
    max_power = max(powers)
    power_range = max_power - min_power if max_power > min_power else 1

    print(f"\nSpectrum scan: {Path(csv_file).name}")
    print(f"Frequency range: {sorted_freqs[0][0]:.1f} - {sorted_freqs[-1][0]:.1f} MHz")
    print(f"Power range: {min_power:.1f} - {max_power:.1f} dBm\n")

    # Simple bar chart
    for freq, power in sorted_freqs[::max(1, len(sorted_freqs) // 50)]:  # Show ~50 points
        normalized = (power - min_power) / power_range
        bar_len = int(normalized * 40)
        bar = "█" * bar_len
        print(f"{freq:7.1f} MHz | {bar:<40} | {power:6.1f} dBm")


def view_peaks_json(json_file):
    """Display detected peaks"""
    with open(json_file) as f:
        data = json.load(f)

    print(f"\nPeaks from scan #{data['scan_num']} ({data['timestamp']})")
    print(f"{'Rank':<5} {'Frequency':<15} {'Power':<10}")
    print("─" * 30)

    for i, peak in enumerate(data['peaks'][:20], 1):
        freq = peak['freq_mhz']
        power = peak['power_dbm']
        print(f"{i:<5} {freq:>8.1f} MHz     {power:>6.1f} dBm")


def main():
    if len(sys.argv) < 2:
        print("Usage: view_scans.py <scan_csv_or_peaks_json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    if path.suffix == ".csv":
        view_spectrum_csv(path)
    elif path.suffix == ".json":
        view_peaks_json(path)
    else:
        print(f"Unknown file type: {path.suffix}")


if __name__ == "__main__":
    main()
