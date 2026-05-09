#!/usr/bin/env python3
"""
wardrive-supervisor — async daemon that manages all collectors.

Replaces wardrive.sh. Designed to run under systemd with:
  Type=notify
  WatchdogSec=60s
  Restart=always
  RestartSec=5s

All errors are caught and logged; the process itself should never crash.
"""

import asyncio
import logging
import logging.handlers
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on sys.path when invoked directly
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from supervisor import config as cfg
from supervisor.collectors.base import HealthState
from supervisor.collectors.esp32 import Esp32Collector
from supervisor.collectors.gps import GpsCollector
from supervisor.collectors.hackrf import HackRFCollector
from supervisor.collectors.rtlsdr import Rtl433Collector, WidebandCollector
from supervisor.collectors.wifi import WifiCollector
from supervisor.db import SupervisorDB
from supervisor.event_bus import bus
from supervisor.registry import Registry
from supervisor.udev_monitor import UdevMonitor

# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Rotating file log alongside the project
    log_dir = cfg.project_root() / "capture" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        log_dir / "supervisor.log", maxBytes=10 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


log = logging.getLogger("wardrive.supervisor")

# ── systemd notify ────────────────────────────────────────────────────────────

def _sd_notify(msg: str) -> None:
    """Fire-and-forget sd_notify message (no dep on python-systemd)."""
    sock_path = os.environ.get("NOTIFY_SOCKET", "")
    if not sock_path:
        return
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(sock_path)
            s.sendall(msg.encode())
    except Exception:
        pass


def _sd_watchdog() -> None:
    _sd_notify("WATCHDOG=1")

# ── Session setup ─────────────────────────────────────────────────────────────

def _make_session_dir() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = cfg.get("SESSION_LABEL", "wardrive")
    capture_root = cfg.resolve_capture_root()
    session_dir = capture_root / "raw" / f"{ts}_{label}"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "logs").mkdir(exist_ok=True)
    log.info("session dir: %s", session_dir)
    return session_dir


def _resolve_db_path() -> Path:
    capture_root = cfg.resolve_capture_root()
    db_dir = capture_root / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "wardrive.db"

# ── Collector factory ─────────────────────────────────────────────────────────

def _build_collectors(settings: dict) -> list:
    collectors = []
    if cfg.get_bool("ENABLE_GPS", True):
        collectors.append(GpsCollector(settings))
    if cfg.get_bool("ENABLE_WIFI", True):
        collectors.append(WifiCollector(settings))
    if cfg.get_bool("ENABLE_ESP32", True):
        collectors.append(Esp32Collector(settings))
    if cfg.get_bool("ENABLE_SDR", True):
        sdr_mode = cfg.get("SDR_MODE", "rtl433").lower()
        if sdr_mode == "wideband":
            collectors.append(WidebandCollector(settings))
            rtl433 = Rtl433Collector(settings)
            rtl433.state = HealthState.DISABLED
            collectors.append(rtl433)
        else:
            collectors.append(Rtl433Collector(settings))
            wb = WidebandCollector(settings)
            wb.state = HealthState.DISABLED
            collectors.append(wb)
    if cfg.get_bool("ENABLE_HACKRF", False):
        collectors.append(HackRFCollector(settings))
    return collectors

# ── Inhibit sleep (Pi/Linux) ──────────────────────────────────────────────────

async def _inhibit_sleep() -> asyncio.subprocess.Process | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemd-inhibit", "--what=idle:sleep:handle-lid-switch",
            "--who=wardrive", "--why=Active capture session", "--mode=block",
            "sleep", "infinity",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        log.info("sleep inhibit active (pid=%d)", proc.pid)
        return proc
    except Exception as exc:
        log.warning("could not inhibit sleep: %s", exc)
        return None

# ── Periodic watchdog ping ────────────────────────────────────────────────────

async def _watchdog_ping_loop() -> None:
    while True:
        _sd_watchdog()
        await asyncio.sleep(15)

# ── SIGHUP: reload config ─────────────────────────────────────────────────────

def _install_sighup() -> None:
    def _reload(signum, frame):
        cfg.load()
        log.info("config reloaded (SIGHUP)")
    signal.signal(signal.SIGHUP, _reload)

# ── Main ──────────────────────────────────────────────────────────────────────

async def _run() -> None:
    cfg.load()
    _setup_logging()
    _install_sighup()
    log.info("wardrive supervisor starting")

    settings = cfg.all_settings()
    session_dir = _make_session_dir()
    db_path = _resolve_db_path()

    # Apply DB migrations before doing anything else
    from processing.migrate import run_migrations
    run_migrations(db_path)

    db = SupervisorDB(db_path)
    registry = Registry(db, session_dir)

    for collector in _build_collectors(settings):
        registry.register(collector)

    udev = UdevMonitor(registry)
    udev.start()

    inhibit_proc = await _inhibit_sleep()
    asyncio.create_task(_watchdog_ping_loop(), name="sd-watchdog")

    # Signal ready to systemd
    _sd_notify("READY=1\nSTATUS=collectors starting")

    shutdown_event = asyncio.Event()

    def _on_signal(signum, frame):
        log.info("received signal %d, shutting down", signum)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)

    try:
        await registry.start_all()
        _sd_notify("STATUS=all collectors running")
        await shutdown_event.wait()
    finally:
        log.info("stopping all collectors")
        _sd_notify("STOPPING=1")
        await registry.stop_all()
        udev.stop()
        if inhibit_proc:
            try:
                inhibit_proc.terminate()
                await asyncio.wait_for(inhibit_proc.wait(), timeout=3)
            except Exception:
                pass
        log.info("supervisor stopped cleanly")


def main() -> None:
    # Catch absolutely everything so systemd can restart us if asyncio itself dies
    try:
        asyncio.run(_run())
    except Exception as exc:
        logging.critical("supervisor crashed unexpectedly: %s", exc, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
