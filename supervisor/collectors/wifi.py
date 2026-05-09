"""
WiFi collector — starts Kismet with monitor-mode setup.
Handles MT7612U and similar chipsets that need the interface pre-created.
"""

import asyncio
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .base import CollectorPlugin

log = logging.getLogger(__name__)

# Interfaces that need airmon-ng pre-creation rather than Kismet's own setup
_PROBLEMATIC_CHIPS = re.compile(r"mt761[0-9]u|mt7612|rtl8812|rtl8814", re.I)


class WifiCollector(CollectorPlugin):
    def __init__(self, cfg: dict):
        super().__init__(
            id="wifi",
            name="WiFi (Kismet)",
            type="kismet",
            # Wifi collectors match on interface, not /dev node
            device_pattern="",
            power_ma=int(cfg.get("power_ma", 400)),
            priority=int(cfg.get("PRIORITY_WIFI", 2)),
        )
        self._mon_iface: str | None = None

    async def pre_launch(self, session_dir: Path, cfg: dict) -> None:
        iface = cfg.get("WIFI_INTERFACE", "wlan1")
        chipset = await _get_chipset(iface)
        if chipset and _PROBLEMATIC_CHIPS.search(chipset):
            log.info("wifi: pre-creating monitor interface for %s (%s)", iface, chipset)
            mon = await _airmon_start(iface)
            if mon:
                self._mon_iface = mon
                log.info("wifi: monitor interface: %s", mon)
            else:
                log.warning("wifi: airmon-ng failed, letting Kismet handle it")
        else:
            self._mon_iface = None

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        iface = cfg.get("WIFI_INTERFACE", "wlan1")
        capture_iface = self._mon_iface or iface
        out_dir = session_dir / "wifi"
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "kismet",
            "--no-ncurses",
            "-c", capture_iface,
            "--log-prefix", str(out_dir / "kismet"),
            "--log-types", "kismet,pcapng",
        ]
        return cmd

    async def post_stop(self, cfg: dict) -> None:
        if self._mon_iface:
            log.info("wifi: stopping monitor interface %s", self._mon_iface)
            await _airmon_stop(self._mon_iface)
            self._mon_iface = None


async def _get_chipset(iface: str) -> str:
    if not shutil.which("airmon-ng"):
        return ""
    try:
        proc = await asyncio.create_subprocess_exec(
            "airmon-ng", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        for line in stdout.decode().splitlines():
            if iface in line:
                return line
    except (asyncio.TimeoutError, Exception):
        pass
    return ""


async def _airmon_start(iface: str) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "airmon-ng", "start", iface,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        text = stdout.decode()
        m = re.search(r"monitor mode (?:enabled|vif enabled) (?:on|for) (\w+)", text, re.I)
        if m:
            return m.group(1)
        # Fallback: common naming convention
        for candidate in (f"{iface}mon", "wlan1mon", "mon0"):
            check = await asyncio.create_subprocess_exec(
                "ip", "link", "show", candidate,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            await check.wait()
            if check.returncode == 0:
                return candidate
    except (asyncio.TimeoutError, Exception) as e:
        log.warning("airmon-ng start failed: %s", e)
    return None


async def _airmon_stop(mon_iface: str) -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "airmon-ng", "stop", mon_iface,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=15)
    except (asyncio.TimeoutError, Exception) as e:
        log.warning("airmon-ng stop failed: %s", e)
