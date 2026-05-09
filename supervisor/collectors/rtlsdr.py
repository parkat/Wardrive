"""
RTL-SDR collector — implements both rtl_433 and wideband modes.
Both share a mutex_group so only one runs at a time.
Active mode is read from config (SDR_MODE) and can be toggled via the UI.
"""

import logging
from pathlib import Path

from .base import CollectorPlugin

log = logging.getLogger(__name__)


class Rtl433Collector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="rtl433",
            name="RTL-SDR (rtl_433)",
            type="sdr",
            device_pattern=r"^/dev/bus/usb/",
            power_ma=int(cfg.get("power_ma", 350)),
            priority=int(cfg.get("PRIORITY_RTL433", 4)),
            mutex_group="rtlsdr",
        )

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        freq = cfg.get("SDR_FREQUENCY_MHZ", "433.92")
        out_dir = session_dir / "sdr"
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_file = out_dir / f"{ts}_rtl433.ndjson"
        return [
            "rtl_433",
            "-f", f"{freq}M",
            "-F", f"json:{out_file}",
            "-M", "time:iso:usec",
            "-M", "level",
        ]


class WidebandCollector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="wideband",
            name="RTL-SDR (wideband)",
            type="sdr",
            device_pattern=r"^/dev/bus/usb/",
            power_ma=int(cfg.get("power_ma", 350)),
            priority=int(cfg.get("PRIORITY_WIDEBAND", 4)),
            mutex_group="rtlsdr",
        )

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        out_dir = session_dir / "sdr"
        out_dir.mkdir(parents=True, exist_ok=True)
        scanner = str(Path(__file__).parent.parent.parent / "processing" / "rtl_wideband.py")
        return [
            "python3", scanner,
            str(out_dir),
            cfg.get("WIDEBAND_FREQ_START_MHZ", "100"),
            cfg.get("WIDEBAND_FREQ_END_MHZ",   "1700"),
            cfg.get("WIDEBAND_SCAN_STEP_MHZ",  "2"),
            cfg.get("WIDEBAND_SCAN_TIME",       "1"),
            cfg.get("WIDEBAND_PEAK_THRESHOLD",  "-40"),
        ]
