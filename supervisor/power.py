"""
USB power management — uhubctl integration and over-current monitoring.
Handles Pi 3B's LAN9514 hub gracefully (limited per-port control).
"""

import asyncio
import json
import logging
import shutil
import subprocess

from . import config as cfg
from .event_bus import bus

log = logging.getLogger(__name__)

_UHUBCTL_AVAILABLE = bool(shutil.which("uhubctl"))


async def query_usb_state() -> list[dict]:
    """Return uhubctl JSON output, or a best-effort placeholder if unavailable."""
    if not _UHUBCTL_AVAILABLE:
        return [{"note": "uhubctl not installed — install with: sudo apt install uhubctl"}]
    try:
        proc = await asyncio.create_subprocess_exec(
            "uhubctl", "-j",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return json.loads(stdout.decode())
    except asyncio.TimeoutError:
        log.warning("uhubctl timed out")
    except Exception as exc:
        log.warning("uhubctl error: %s", exc)
    return []


async def cycle_port(location: str) -> bool:
    """
    Power-cycle a USB port identified by uhubctl location string.
    On Pi 3B (LAN9514) this may cycle all ports together — acceptable
    for recovery since we re-probe all collectors after the cycle.
    """
    if not _UHUBCTL_AVAILABLE:
        log.warning("uhubctl not available — cannot cycle port %s", location)
        return False
    for action in ("off", "on"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "uhubctl", "-l", location, "-a", action,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            log.warning("uhubctl %s timed out for port %s", action, location)
            return False
        except Exception as exc:
            log.warning("uhubctl %s failed: %s", action, exc)
            return False
        if action == "off":
            await asyncio.sleep(1)   # brief off time before power-on

    await bus.emit("usb_port_cycled", {"port": location})
    log.info("USB port %s cycled", location)
    return True


async def on_overcurrent(device_path: str) -> None:
    """Called by the udev monitor when an over-current condition is detected."""
    log.error("USB over-current detected on %s — cycling port", device_path)
    await bus.emit("overcurrent", {"device": device_path})
    # Best effort: try to find and cycle the hub location
    state = await query_usb_state()
    for hub in state if isinstance(state, list) else []:
        loc = hub.get("hub", "")
        if loc:
            await cycle_port(loc)
            break
