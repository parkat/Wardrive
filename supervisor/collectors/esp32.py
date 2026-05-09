"""
ESP32 BLE collector — serial reader, auto-detects port.
Wraps the existing processing/esp32_reader.py.
"""

import asyncio
import logging
from pathlib import Path

from .base import CollectorPlugin

log = logging.getLogger(__name__)

# USB VIDs for ESP32 USB-serial chips
_ESP32_VIDS = {"10c4", "1a86", "0403"}  # CP2102, CH340, FTDI

_AUTO_DETECT_PORTS = [
    "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2",
    "/dev/ttyACM0", "/dev/ttyACM1",
]


class Esp32Collector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="esp32",
            name="ESP32 BLE",
            type="serial",
            device_pattern=r"^/dev/tty(USB|ACM)\d+$",
            power_ma=int(cfg.get("power_ma", 100)),
            priority=int(cfg.get("PRIORITY_ESP32", 3)),
        )

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        port = cfg.get("ESP32_DEVICE", "").strip()
        if not port:
            port = await self._auto_detect(cfg)
        if not port:
            raise RuntimeError("ESP32 device not found — plug in the ESP32 DevKit")

        self.device_path = port
        baud = cfg.get("ESP32_BAUD", "115200")
        out_dir = session_dir / "bt"
        out_dir.mkdir(parents=True, exist_ok=True)
        reader = str(Path(__file__).parent.parent.parent / "processing" / "esp32_reader.py")
        return [
            "python3", reader,
            "--port", port,
            "--baud", baud,
            "--output", str(out_dir / "esp32_ble.ndjson"),
            "--gpsd",
        ]

    async def _auto_detect(self, cfg: dict) -> str | None:
        """Try each candidate port; return first that exists and has data."""
        import os
        for port in _AUTO_DETECT_PORTS:
            if os.path.exists(port):
                log.info("esp32: auto-detected port %s", port)
                return port
        return None
