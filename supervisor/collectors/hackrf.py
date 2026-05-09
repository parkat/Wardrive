"""
HackRF One collector stub.
Activates when ENABLE_HACKRF=true in config and hardware is present.
Full implementation deferred until hardware arrives.
"""

import logging
from pathlib import Path

from .base import CollectorPlugin, HealthState

log = logging.getLogger(__name__)


class HackRFCollector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="hackrf",
            name="HackRF One (stub)",
            type="hackrf",
            device_pattern=r"^/dev/bus/usb/",
            power_ma=int(cfg.get("power_ma", 500)),
            priority=int(cfg.get("PRIORITY_HACKRF", 5)),
        )

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        out_dir = session_dir / "hackrf"
        out_dir.mkdir(parents=True, exist_ok=True)
        scanner = str(Path(__file__).parent.parent.parent / "processing" / "hackrf_scanner.py")
        return ["python3", scanner, "--out", str(out_dir)]
