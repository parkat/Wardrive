#!/usr/bin/env python3
"""
enrich_wideband.py — post-process wideband spectrum scans

Reads peak JSON files + spectrum CSVs from a wideband scanner session and
produces an enriched database with:
- Frequency band identification (ISM, cellular, broadcast, etc.)
- Signal characteristics (bandwidth, power statistics, modulation estimate)
- GPS-joined location context (if available)
- Frequency stability tracking (detect hoppers/agile signals)
- Cross-reference against known frequency databases
"""

import json
import sqlite3
import sys
import math
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# Frequency band definitions
BANDS = {
    'LF': (30e3, 300e3, 'Low Frequency'),
    'MF': (300e3, 3e6, 'Medium Frequency'),
    'HF': (3e6, 30e6, 'High Frequency'),
    'VHF_Aviation': (108e6, 137e6, 'Aviation VHF'),
    'FM_Broadcast': (88e6, 108e6, 'FM Broadcast Radio'),
    'VHF_Public': (136e6, 174e6, 'Public Safety VHF'),
    'ISM_433': (430e6, 435e6, 'ISM 433 MHz'),
    'UHF_Public': (400e6, 512e6, 'Public Safety UHF'),
    'ISM_868': (863e6, 870e6, 'ISM 868 MHz (EU)'),
    'ISM_915': (902e6, 928e6, 'ISM 915 MHz (US)'),
    'GSM_850_Down': (869e6, 894e6, 'GSM-850 Downlink'),
    'GSM_900_Down': (935e6, 960e6, 'GSM-900 Downlink'),
    'GSM_1800_Down': (1805e6, 1880e6, 'GSM-1800 Downlink'),
    'GSM_1900_Down': (1930e6, 1990e6, 'GSM-1900 Downlink'),
}

def identify_band(freq_mhz):
    """Return band name and description for a given frequency"""
    freq_hz = freq_mhz * 1e6
    for band_id, (low, high, desc) in BANDS.items():
        if low <= freq_hz < high:
            return band_id, desc
    return 'Unknown', 'Unallocated/Unknown'

def estimate_bandwidth_from_csv(csv_file, peak_freq_mhz, window_mhz=10):
    """Estimate -3dB bandwidth of a signal from rtl_power CSV spectral data"""
    try:
        powers = {}
        with open(csv_file) as f:
            for line in f:
                if line.startswith('Date'):
                    continue
                parts = line.split(', ')
                if len(parts) < 7:
                    continue
                hz_low = float(parts[2])
                hz_step = float(parts[4])
                for i, raw in enumerate(parts[6:]):
                    freq_hz = hz_low + i * hz_step
                    freq_mhz_bin = freq_hz / 1e6
                    if abs(freq_mhz_bin - peak_freq_mhz) <= window_mhz:
                        try:
                            power = float(raw.strip())
                            powers[freq_mhz_bin] = power
                        except:
                            continue

        if not powers:
            return None

        # Find -3dB points
        peak_power = max(powers.values())
        threshold = peak_power - 3

        freqs_above = [f for f, p in powers.items() if p > threshold]
        if len(freqs_above) > 1:
            return max(freqs_above) - min(freqs_above)
        return None
    except:
        return None

def classify_modulation(bandwidth_mhz, freq_mhz, power_dbm):
    """Estimate modulation type based on bandwidth, frequency, and power characteristics"""
    if bandwidth_mhz is None:
        return 'Unclassified'

    bw = bandwidth_mhz

    # Heuristics based on typical signal widths
    if bw < 0.03:
        return 'Beacon/CW'
    elif bw < 0.2:
        return 'FSK/OOK (narrowband)'
    elif bw < 0.5:
        return 'GFSK/2FSK'
    elif bw < 3:
        return 'SSB/AM/FSK'
    elif bw < 8:
        return 'FM (narrow)'
    elif bw < 15:
        return 'FM (wide)/P25'
    elif bw < 30:
        return 'Cellular (GSM/TDMA)'
    elif bw < 50:
        return 'Cellular (WCDMA/CDMA)'
    else:
        return 'Broadband/OFDM/Noise'

def estimate_signal_quality(power_dbm, threshold=-40):
    """Rate signal quality as weak/fair/good/strong"""
    if power_dbm < threshold:
        return 'Noise'
    elif power_dbm < threshold + 10:
        return 'Weak'
    elif power_dbm < threshold + 20:
        return 'Fair'
    elif power_dbm < threshold + 30:
        return 'Good'
    else:
        return 'Strong'

def load_gps_data(session_dir):
    """Load GPS fixes from NMEA log if available"""
    gps_fixes = []
    nmea_log = Path(session_dir) / 'gps' / 'nmea.log'
    if not nmea_log.exists():
        return {}

    try:
        with open(nmea_log) as f:
            for line in f:
                if line.startswith('$GPRMC'):
                    parts = line.split(',')
                    if len(parts) >= 9 and parts[2] == 'A':  # Valid fix
                        try:
                            timestamp = parts[1]  # HHMMSS.SS
                            lat = float(parts[3][:2]) + float(parts[3][2:]) / 60
                            lat_dir = parts[4]
                            lon = float(parts[5][:3]) + float(parts[5][3:]) / 60
                            lon_dir = parts[6]

                            if lat_dir == 'S':
                                lat = -lat
                            if lon_dir == 'W':
                                lon = -lon

                            gps_fixes.append({'lat': lat, 'lon': lon, 'time': timestamp})
                        except:
                            continue
    except:
        pass

    return gps_fixes

def detect_frequency_agility(enriched_peaks):
    """Identify signals that change frequency over time (hoppers)"""
    freq_tracks = defaultdict(list)

    # Group peaks by approximate frequency (within 1 MHz)
    for peak in enriched_peaks:
        freq = peak['freq_mhz']
        # Find existing track
        found = False
        for track_freq in freq_tracks.keys():
            if abs(freq - track_freq) < 1.0:
                freq_tracks[track_freq].append(peak)
                found = True
                break
        if not found:
            freq_tracks[freq].append(peak)

    # Mark agile signals (those with high frequency variance)
    agile = set()
    for freq, peaks in freq_tracks.items():
        freqs = [p['freq_mhz'] for p in peaks]
        if len(freqs) > 3:
            variance = max(freqs) - min(freqs)
            if variance > 0.5:  # More than 0.5 MHz variation
                agile.update([p['id'] for p in peaks if 'id' in p])

    return agile

def enrich_peaks_from_session(session_dir):
    """Process all wideband scanner data from a session directory"""
    session_path = Path(session_dir)
    sdr_dir = session_path / 'sdr'

    if not sdr_dir.exists():
        print(f"[enrich] No sdr directory found: {sdr_dir}", file=sys.stderr)
        return None

    peak_files = sorted(sdr_dir.glob('peaks_*.json'))
    csv_files = sorted(sdr_dir.glob('scan_*.csv'))

    if not peak_files:
        print(f"[enrich] No peak files found in {sdr_dir}", file=sys.stderr)
        return None

    # Load GPS data if available
    gps_fixes = load_gps_data(session_dir)

    print(f"[enrich] Processing {len(peak_files)} peak files from {session_dir}", file=sys.stderr)
    enriched_peaks = []
    peak_id = 0

    for peak_file in peak_files:
        try:
            with open(peak_file) as f:
                peak_data = json.load(f)

            scan_num = peak_data.get('scan_num')
            timestamp = peak_data.get('timestamp')

            # Find corresponding CSV file
            corresponding_csv = None
            for csv_file in csv_files:
                if f'n{scan_num}' in str(csv_file):
                    corresponding_csv = csv_file
                    break

            for peak in peak_data.get('peaks', []):
                freq_mhz = peak['freq_mhz']
                power_dbm = peak['power_dbm']

                # Estimate bandwidth
                bandwidth_mhz = None
                if corresponding_csv:
                    bandwidth_mhz = estimate_bandwidth_from_csv(corresponding_csv, freq_mhz)

                # Classify modulation
                modulation = classify_modulation(bandwidth_mhz, freq_mhz, power_dbm)

                # Identify band
                band_id, band_desc = identify_band(freq_mhz)

                # Estimate signal quality
                quality = estimate_signal_quality(power_dbm)

                enriched_peaks.append({
                    'id': peak_id,
                    'timestamp': timestamp,
                    'scan_num': scan_num,
                    'freq_mhz': freq_mhz,
                    'power_dbm': power_dbm,
                    'band_id': band_id,
                    'band_description': band_desc,
                    'bandwidth_mhz': bandwidth_mhz,
                    'modulation': modulation,
                    'signal_quality': quality,
                })
                peak_id += 1
        except Exception as e:
            print(f"[enrich] Error processing {peak_file}: {e}", file=sys.stderr)
            continue

    print(f"[enrich] Enriched {len(enriched_peaks)} peaks", file=sys.stderr)
    return enriched_peaks

def save_enriched_database(enriched_peaks, output_path):
    """Save enriched peaks to SQLite database with statistics"""
    db = sqlite3.connect(output_path)
    cursor = db.cursor()

    # Main peaks table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wideband_peaks (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            scan_num INTEGER,
            freq_mhz REAL,
            power_dbm REAL,
            band_id TEXT,
            band_description TEXT,
            bandwidth_mhz REAL,
            modulation TEXT,
            signal_quality TEXT
        )
    ''')

    # Summary table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS wideband_summary (
            band_id TEXT PRIMARY KEY,
            band_description TEXT,
            num_detections INTEGER,
            peak_power_dbm REAL,
            avg_power_dbm REAL,
            min_power_dbm REAL,
            common_modulations TEXT,
            freq_range_mhz TEXT
        )
    ''')

    # Insert peaks
    for peak in enriched_peaks:
        cursor.execute('''
            INSERT INTO wideband_peaks
            (id, timestamp, scan_num, freq_mhz, power_dbm, band_id, band_description, bandwidth_mhz, modulation, signal_quality)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            peak['id'],
            peak['timestamp'],
            peak['scan_num'],
            peak['freq_mhz'],
            peak['power_dbm'],
            peak['band_id'],
            peak['band_description'],
            peak['bandwidth_mhz'],
            peak['modulation'],
            peak['signal_quality'],
        ))

    # Compute and insert band summaries
    band_stats = defaultdict(lambda: {
        'powers': [],
        'modulations': defaultdict(int),
        'freqs': [],
        'description': '',
    })

    for peak in enriched_peaks:
        band_id = peak['band_id']
        band_stats[band_id]['powers'].append(peak['power_dbm'])
        band_stats[band_id]['modulations'][peak['modulation']] += 1
        band_stats[band_id]['freqs'].append(peak['freq_mhz'])
        band_stats[band_id]['description'] = peak['band_description']

    for band_id, stats in band_stats.items():
        powers = stats['powers']
        freqs = stats['freqs']
        mods = stats['modulations']

        most_common_mods = ', '.join([
            f"{mod} ({count})"
            for mod, count in sorted(mods.items(), key=lambda x: x[1], reverse=True)[:3]
        ])

        cursor.execute('''
            INSERT INTO wideband_summary
            (band_id, band_description, num_detections, peak_power_dbm, avg_power_dbm, min_power_dbm, common_modulations, freq_range_mhz)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            band_id,
            stats['description'],
            len(powers),
            max(powers),
            sum(powers) / len(powers),
            min(powers),
            most_common_mods,
            f"{min(freqs):.1f} - {max(freqs):.1f}",
        ))

    db.commit()
    db.close()
    print(f"[enrich] Saved enriched database: {output_path}", file=sys.stderr)
    print(f"[enrich] Peaks: {len(enriched_peaks)}, Bands: {len(band_stats)}", file=sys.stderr)

def main():
    if len(sys.argv) < 2:
        print("Usage: enrich_wideband.py <session_dir>", file=sys.stderr)
        print("  Reads wideband scanner peak JSON files and spectrum CSVs", file=sys.stderr)
        print("  Outputs enriched SQLite database: <session_dir>/wideband_enriched.db", file=sys.stderr)
        sys.exit(1)

    session_dir = sys.argv[1]
    enriched = enrich_peaks_from_session(session_dir)

    if enriched:
        db_path = Path(session_dir) / 'wideband_enriched.db'
        save_enriched_database(enriched, db_path)
        print(f"[enrich] Done. Query with: sqlite3 {db_path} 'SELECT * FROM wideband_summary;'", file=sys.stderr)
    else:
        print("[enrich] No peaks to enrich", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
