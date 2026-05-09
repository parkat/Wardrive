"""GPS collector — starts gpsd and gpspipe NMEA logger."""

import asyncio
import logging
import shutil
from pathlib import Path

from .base import CollectorPlugin

log = logging.getLogger(__name__)


class GpsCollector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="gps",
            name="GPS (gpsd)",
            type="gps",
            device_pattern=r"^/dev/tty(ACM|USB)\d+$",
            power_ma=int(cfg.get("power_ma", 50)),
            priority=int(cfg.get("PRIORITY_GPS", 1)),
        )

    async def pre_launch(self, session_dir: Path, cfg: dict) -> None:
        device = cfg.get("GPS_DEVICE", "/dev/ttyACM0")
        # Ensure gpsd is pointing at the right device
        if shutil.which("gpsd"):
            proc = await asyncio.create_subprocess_exec(
                "gpsd", "-n", "-b", device,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        out_dir = session_dir / "gps"
        out_dir.mkdir(parents=True, exist_ok=True)
        nmea_log = out_dir / "nmea.log"
        return ["gpspipe", "-r", "-o", str(nmea_log)]
