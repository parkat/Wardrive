# Wideband Spectrum Scanning & Enrichment

The warDrive wideband scanner passively scans 100–1700 MHz (R820T hardware range) and logs all detected signals. All analysis happens in post-processing via the enrichment pipeline.

## Quick Start

Enable wideband scanning in `config/wardrive.conf`:

```bash
ENABLE_SDR=false                    # Disable rtl_433 (shares the RTL-SDR dongle)
ENABLE_WIDEBAND_SDR=true            # Enable wideband scanner
```

Run a capture session:
```bash
sudo ./wardrive.sh
```

This will continuously scan 100–1700 MHz in ~13-minute cycles (default settings) and log:
- Peak JSON files with detected signals
- Spectrum CSV files with raw rtl_power data

## Post-Processing: Enrichment

After the drive/session ends, run the enrichment pipeline:

```bash
python3 processing/enrich_wideband.py capture/raw/SESSION_TIMESTAMP/
```

This produces: `capture/raw/SESSION_TIMESTAMP/wideband_enriched.db`

The enriched database contains two tables:

### `wideband_peaks` — Per-detection details
```sql
SELECT freq_mhz, power_dbm, band_id, modulation, signal_quality, bandwidth_mhz
FROM wideband_peaks
WHERE band_id = 'ISM_433'
ORDER BY power_dbm DESC;
```

Columns:
- `freq_mhz`: Center frequency
- `power_dbm`: Signal strength  
- `band_id`: Frequency band (ISM_433, GSM_900_Down, FM_Broadcast, etc.)
- `band_description`: Human-readable band name
- `modulation`: Estimated modulation type (FM, SSB, GFSK, etc.)
- `bandwidth_mhz`: Estimated -3dB bandwidth
- `signal_quality`: Weak/Fair/Good/Strong

### `wideband_summary` — Band statistics
```sql
SELECT band_id, num_detections, peak_power_dbm, avg_power_dbm, common_modulations
FROM wideband_summary
ORDER BY peak_power_dbm DESC;
```

Example output:
```
ISM_433|ISM 433 MHz|342|-7.2|-15.3|SSB/AM/FSK (240), GFSK/2FSK (102)
FM_Broadcast|FM Broadcast Radio|158|-2.1|-18.5|FM (wide) (158)
GSM_900_Down|GSM-900 Downlink|87|-8.4|-22.1|Cellular (GSM/TDMA) (87)
```

## Configuration Tuning

In `config/wardrive.conf`:

```bash
WIDEBAND_FREQ_START_MHZ=100         # Start frequency (R820T min ~24 MHz)
WIDEBAND_FREQ_END_MHZ=1700          # End frequency (R820T max ~1766 MHz)
WIDEBAND_SCAN_STEP_MHZ=2            # Frequency resolution in MHz
  # Step=2: (1700-100)/2 = 800 hops × 1s = ~13 min per sweep
  # Step=5: ~5 min per sweep (coarser resolution)
  # Step=1: ~26 min per sweep (finer resolution, slow)
WIDEBAND_SCAN_TIME=1                # Integration time per step in seconds
  # Must be a positive integer; 1s is typical
WIDEBAND_PEAK_THRESHOLD=-40         # dBm threshold for logging peaks
  # -40 dBm: captures most signals
  # -30 dBm: only strong signals
  # -50 dBm: includes weak signals (more noise)
```

## Signal Analysis Details

### Bandwidth Estimation
The enrichment module estimates signal bandwidth from the rtl_power CSV spectral data using -3dB points. This is used to classify the modulation.

### Modulation Classification
Based on bandwidth heuristics:
- <30 kHz → Beacon/CW (continuous wave)
- 30–200 kHz → FSK/OOK (On-Off Keying)
- 200 kHz–3 MHz → SSB/AM/FSK
- 3–8 MHz → FM (narrow)
- 8–15 MHz → FM (wide)/P25 digital
- 15–30 MHz → Cellular (GSM/TDMA)
- 30–50 MHz → Cellular (WCDMA/CDMA)
- >50 MHz → Broadband/OFDM/Noise

**Note:** These are educated guesses based on signal width. Accurate modulation identification requires deeper analysis (e.g., constellation diagrams).

### Frequency Bands
Recognized bands include:
- **ISM**: 433 MHz, 868 MHz (EU), 915 MHz (US)
- **Cellular**: GSM-850, GSM-900, GSM-1800, GSM-1900 downlinks
- **Broadcast**: FM broadcast (88–108 MHz)
- **Aviation**: 108–137 MHz
- **Public Safety**: VHF (136–174 MHz), UHF (400–512 MHz)

## Use Cases

### Example 1: Find Interesting Signals
```sql
-- Strongest signals across all bands
SELECT band_id, freq_mhz, power_dbm, modulation
FROM wideband_peaks
ORDER BY power_dbm DESC
LIMIT 20;

-- Signals in the ISM bands (IoT devices, etc.)
SELECT freq_mhz, power_dbm, COUNT(*) as detections
FROM wideband_peaks
WHERE band_id IN ('ISM_433', 'ISM_868', 'ISM_915')
GROUP BY freq_mhz
ORDER BY detections DESC;
```

### Example 2: Identify Cellular Activity
```sql
-- GSM downlink activity
SELECT freq_mhz, power_dbm, COUNT(*) as consistent_detections
FROM wideband_peaks
WHERE band_id LIKE 'GSM_%Down'
GROUP BY freq_mhz
ORDER BY consistent_detections DESC;
```

### Example 3: Modulation Composition
```sql
-- What modulation types are we seeing?
SELECT modulation, COUNT(*) as count, AVG(power_dbm) as avg_power
FROM wideband_peaks
GROUP BY modulation
ORDER BY count DESC;
```

## Limitations & Future Improvements

- **Modulation classification** is heuristic-based (bandwidth-only). Accurate classification requires IQ sample analysis.
- **No GPS joining** yet; future version will map signals to GPS coordinates.
- **No frequency agility tracking** yet; can't detect frequency-hopping signals.
- **No correlation** with external databases (FCC allocations, tower locations, etc.).

## Files Reference

- `processing/rtl_wideband.py` — Spectrum scanner (calls rtl_power, logs peaks)
- `processing/enrich_wideband.py` — Post-processing enrichment pipeline
- `config/wardrive.conf` → `ENABLE_WIDEBAND_SDR` configuration
- Output: `capture/raw/SESSION/sdr/` (CSV + JSON + DB)
