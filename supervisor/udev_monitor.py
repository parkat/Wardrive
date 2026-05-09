"""
udev event monitor — listens for USB add/remove events and notifies the registry.
Uses pyudev with an asyncio bridge. Falls back gracefully if pyudev is missing.
"""

import asyncio
import logging
import re

from .event_bus import bus

log = logging.getLogger(__name__)

try:
    import pyudev
    _PYUDEV_OK = True
except ImportError:
    _PYUDEV_OK = False
    log.warning("pyudev not installed — udev USB detection disabled. "
                 "Install with: sudo apt install python3-pyudev")


class UdevMonitor:
    def __init__(self, registry) -> None:
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observer = None

    def start(self) -> None:
        if not _PYUDEV_OK:
            return
        self._loop = asyncio.get_event_loop()
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by("usb")
        monitor.start()

        self._observer = pyudev.MonitorObserver(monitor, self._on_event, name="udev-observer")
        self._observer.daemon = True
        self._observer.start()
        log.info("udev monitor started")

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()

    def _on_event(self, device) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        action  = device.action
        devnode = device.get("DEVNAME", "")
        driver  = device.get("DRIVER", "")

        if "over_current" in driver.lower() or "overcurrent" in str(device.get("DEVPATH", "")).lower():
            asyncio.run_coroutine_threadsafe(
                self._handle_overcurrent(devnode), self._loop
            )
            return

        if action == "add" and devnode:
            asyncio.run_coroutine_threadsafe(
                self._handle_add(devnode, device), self._loop
            )
        elif action == "remove" and devnode:
            asyncio.run_coroutine_threadsafe(
                self._handle_remove(devnode), self._loop
            )

    async def _handle_add(self, devnode: str, device) -> None:
        log.info("udev ADD: %s", devnode)
        await bus.emit("usb_add", {"device": devnode})

        # Notify any collectors whose device_pattern matches
        for collector in self._registry.collectors.values():
            if collector.device_pattern and re.search(collector.device_pattern, devnode):
                from .collectors.base import HealthState
                if collector.state == HealthState.UNAVAILABLE:
                    log.info("udev: device %s matches %s — enabling", devnode, collector.id)
                    collector.device_path = devnode
                    await self._registry.enable_collector(collector.id)

    async def _handle_remove(self, devnode: str) -> None:
        log.info("udev REMOVE: %s", devnode)
        await bus.emit("usb_remove", {"device": devnode})

        for collector in self._registry.collectors.values():
            if collector.device_path == devnode:
                log.warning("udev: device %s for %s removed — stopping collector",
                            devnode, collector.id)
                from .collectors.base import HealthState
                await self._registry.disable_collector(collector.id, reason="udev_remove")
                collector.device_path = None
                # After a short delay, re-enable so it waits for re-plug
                await asyncio.sleep(3)
                await self._registry.enable_collector(collector.id)

    async def _handle_overcurrent(self, devnode: str) -> None:
        from .power import on_overcurrent
        await on_overcurrent(devnode)
