"""
Collector registry — owns the authoritative set of CollectorPlugin instances
and the session-level state machine.

Thread-safety: all public methods must be called from the asyncio event loop.
"""

import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

from . import config as cfg
from .collectors.base import CollectorPlugin, HealthState
from .event_bus import bus

log = logging.getLogger(__name__)

# How often to run the hang-detector and budget check
WATCHDOG_INTERVAL = 15   # seconds
# How often to log a brief alive heartbeat
HEARTBEAT_INTERVAL = 60  # seconds


class Registry:
    def __init__(self, db, session_dir: Path) -> None:
        self._db = db
        self._session_dir = session_dir
        self._collectors: dict[str, CollectorPlugin] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    # ── Collector management ────────────────────────────────────────────────────

    def register(self, collector: CollectorPlugin) -> None:
        self._collectors[collector.id] = collector

    @property
    def collectors(self) -> dict[str, CollectorPlugin]:
        return self._collectors

    def get(self, cid: str) -> Optional[CollectorPlugin]:
        return self._collectors.get(cid)

    # ── Lifecycle ────────────────────────────────────────────────────────────────

    async def start_all(self) -> None:
        self._running = True
        for cid, collector in self._collectors.items():
            if collector.state != HealthState.DISABLED:
                self._tasks[cid] = asyncio.create_task(
                    self._run_collector(collector), name=f"collector-{cid}"
                )
        asyncio.create_task(self._watchdog_loop(), name="watchdog")
        asyncio.create_task(self._heartbeat_loop(), name="heartbeat")

    async def stop_all(self) -> None:
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        # Kill any still-running processes
        for collector in self._collectors.values():
            await self._kill_process(collector, timeout=5)

    # ── Per-collector supervisor loop ────────────────────────────────────────────

    async def _run_collector(self, c: CollectorPlugin) -> None:
        """
        Supervisor loop for a single collector.
        Implements exponential backoff and max-retry logic.
        Never raises — all exceptions are caught and logged.
        """
        backoff = cfg.get_int("RESTART_BACKOFF_INITIAL", 2)
        max_backoff = cfg.get_int("RESTART_BACKOFF_MAX", 60)
        restart_max = cfg.get_int("RESTART_MAX", 10)
        reset_after = cfg.get_int("RESTART_RESET_AFTER", 300)
        last_stable_start: float = 0.0

        while self._running and c.state != HealthState.DISABLED:
            # ── Check mutex ──────────────────────────────────────────────────
            if c.mutex_group and self._mutex_occupied(c):
                log.debug("%s: mutex group %s occupied, waiting", c.id, c.mutex_group)
                await asyncio.sleep(5)
                continue

            # ── Check power budget ───────────────────────────────────────────
            if not self._budget_allows(c):
                log.warning("%s: USB budget exceeded, staying UNAVAILABLE", c.id)
                await self._set_state(c, HealthState.UNAVAILABLE)
                await asyncio.sleep(30)
                continue

            # ── Build command ────────────────────────────────────────────────
            try:
                await c.pre_launch(self._session_dir, cfg.all_settings())
                argv = await c.build_command(self._session_dir, cfg.all_settings())
            except Exception as exc:
                log.error("%s: build_command failed: %s", c.id, exc)
                c.last_error = str(exc)
                await self._set_state(c, HealthState.UNAVAILABLE)
                await asyncio.sleep(30)
                continue

            # ── Launch process ───────────────────────────────────────────────
            log_file = self._session_dir / "logs" / f"{c.id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)

            await self._set_state(c, HealthState.STARTING)
            process = None
            try:
                with open(log_file, "ab") as log_fh:
                    process = await asyncio.create_subprocess_exec(
                        *argv,
                        stdout=log_fh,
                        stderr=log_fh,
                        # New process group so we can kill the entire tree
                        start_new_session=True,
                    )
                c._process = process
                c.pid = process.pid
                c.started_at = time.time()
                c.touch_output()
                last_stable_start = time.monotonic()
                await self._set_state(c, HealthState.RUNNING)
                log.info("%s: started pid=%d argv=%s", c.id, process.pid, argv[0])

                await self._db.log_event(c.id, "start", {"pid": process.pid, "argv": argv[0]})

                # ── Wait for exit ────────────────────────────────────────────
                exit_code = await process.wait()

            except asyncio.CancelledError:
                await self._kill_process(c, timeout=5)
                await c.post_stop(cfg.all_settings())
                return
            except Exception as exc:
                log.error("%s: failed to launch: %s", c.id, exc)
                c.last_error = str(exc)
                exit_code = -1
            finally:
                c._process = None
                c.pid = None

            # ── Process exited ───────────────────────────────────────────────
            c.last_exit_code = exit_code
            await c.post_stop(cfg.all_settings())

            if not self._running or c.state == HealthState.DISABLED:
                return

            await self._set_state(c, HealthState.CRASHED)
            c.restart_count += 1

            # Reset backoff if it was running stably for reset_after seconds
            if time.monotonic() - last_stable_start > reset_after:
                backoff = cfg.get_int("RESTART_BACKOFF_INITIAL", 2)
                c.restart_count = 0

            await self._db.log_event(c.id, "crash", {
                "exit_code": exit_code, "restart_count": c.restart_count
            })

            if c.restart_count > restart_max:
                log.error("%s: exceeded max restarts (%d), marking UNAVAILABLE", c.id, restart_max)
                await self._set_state(c, HealthState.UNAVAILABLE)
                return

            log.warning("%s: crashed (exit=%s), restarting in %ds (attempt %d/%d)",
                        c.id, exit_code, backoff, c.restart_count, restart_max)

            # Interruptible sleep so DISABLED state or shutdown cancels it
            try:
                await asyncio.wait_for(self._wait_while_running(c), timeout=backoff)
            except asyncio.TimeoutError:
                pass

            backoff = min(backoff * 2, max_backoff)

    async def _wait_while_running(self, c: CollectorPlugin) -> None:
        while self._running and c.state != HealthState.DISABLED:
            await asyncio.sleep(1)

    # ── Watchdog ─────────────────────────────────────────────────────────────────

    async def _watchdog_loop(self) -> None:
        hang_timeout = cfg.get_int("HANG_TIMEOUT", 120)
        while self._running:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            for c in self._collectors.values():
                if c.state != HealthState.RUNNING:
                    continue
                # Hang detection: if no output for hang_timeout seconds, kill it
                # (the supervisor loop will restart it with backoff)
                if c.seconds_since_output() > hang_timeout:
                    log.warning("%s: hung (%ds no output), killing", c.id, hang_timeout)
                    c.last_error = f"hung: {hang_timeout}s no output"
                    await self._db.log_event(c.id, "crash", {"reason": "hang"})
                    await self._kill_process(c, timeout=3)

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            running = [c.id for c in self._collectors.values() if c.state == HealthState.RUNNING]
            crashed = [c.id for c in self._collectors.values() if c.state in (
                HealthState.CRASHED, HealthState.UNAVAILABLE)]
            log.info("heartbeat: running=%s unavailable=%s", running, crashed)

    # ── State helpers ─────────────────────────────────────────────────────────

    async def _set_state(self, c: CollectorPlugin, state: HealthState) -> None:
        old = c.state
        c.state = state
        if old != state:
            await bus.emit("collector_state_change", c.to_dict())

    async def _kill_process(self, c: CollectorPlugin, timeout: int = 5) -> None:
        proc = c._process
        if proc is None:
            return
        try:
            # Kill the entire process group
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
                await proc.wait()
        except ProcessLookupError:
            pass
        except Exception as exc:
            log.debug("kill %s: %s", c.id, exc)
        c._process = None
        c.pid = None

    # ── Power budget ──────────────────────────────────────────────────────────

    def _budget_allows(self, candidate: CollectorPlugin) -> bool:
        budget = cfg.get_int("USB_BUDGET_MA", 900)
        in_use = sum(
            c.power_ma for c in self._collectors.values()
            if c.state == HealthState.RUNNING and c.id != candidate.id
        )
        if in_use + candidate.power_ma > budget:
            # Try to shed the lowest-priority running collector to make room
            running = sorted(
                [c for c in self._collectors.values() if c.state == HealthState.RUNNING],
                key=lambda x: -x.priority,  # highest priority number = shed first
            )
            for victim in running:
                if victim.priority > candidate.priority:
                    log.warning("power budget: shedding %s to make room for %s",
                                victim.id, candidate.id)
                    asyncio.create_task(self.disable_collector(
                        victim.id, reason="power_budget"))
                    return True
            return False
        return True

    def _mutex_occupied(self, candidate: CollectorPlugin) -> bool:
        for c in self._collectors.values():
            if (c.id != candidate.id
                    and c.mutex_group == candidate.mutex_group
                    and c.state == HealthState.RUNNING):
                return True
        return False

    # ── External control ──────────────────────────────────────────────────────

    async def disable_collector(self, cid: str, reason: str = "manual") -> None:
        c = self._collectors.get(cid)
        if not c:
            return
        log.info("disabling collector %s (reason: %s)", cid, reason)
        await self._set_state(c, HealthState.DISABLED)
        await self._kill_process(c, timeout=5)
        await self._db.log_event(cid, "disabled", {"reason": reason})
        task = self._tasks.get(cid)
        if task:
            task.cancel()
            self._tasks.pop(cid, None)

    async def enable_collector(self, cid: str) -> None:
        c = self._collectors.get(cid)
        if not c:
            return
        log.info("enabling collector %s", cid)
        c.restart_count = 0
        c.last_error = None
        await self._set_state(c, HealthState.UNAVAILABLE)
        self._tasks[cid] = asyncio.create_task(
            self._run_collector(c), name=f"collector-{cid}"
        )
        await self._db.log_event(cid, "enabled", {})

    async def restart_collector(self, cid: str) -> None:
        c = self._collectors.get(cid)
        if not c:
            return
        log.info("force-restarting collector %s", cid)
        await self._kill_process(c, timeout=5)
        # The supervisor loop will pick it back up automatically

    async def set_sdr_mode(self, mode: str) -> None:
        """Toggle RTL-SDR between 'rtl433' and 'wideband'."""
        if mode not in ("rtl433", "wideband"):
            raise ValueError(f"invalid SDR mode: {mode}")
        disable_id = "rtl433" if mode == "wideband" else "wideband"
        enable_id  = mode
        c_dis = self._collectors.get(disable_id)
        c_en  = self._collectors.get(enable_id)
        if c_dis and c_dis.state not in (HealthState.DISABLED, HealthState.UNAVAILABLE):
            await self.disable_collector(disable_id, reason="sdr_mode_switch")
        if c_en:
            await self.enable_collector(enable_id)
        # Persist choice so it survives supervisor restart
        _rewrite_conf_value("SDR_MODE", mode)
        await bus.emit("sdr_mode_changed", {"mode": mode})

    def alive_count(self) -> int:
        return sum(1 for c in self._collectors.values()
                   if c.state == HealthState.RUNNING)


def _rewrite_conf_value(key: str, value: str) -> None:
    """Update a single key in wardrive.conf in place."""
    import re
    conf_path = cfg.project_root() / "config" / "wardrive.conf"
    try:
        text = conf_path.read_text()
        new_text = re.sub(
            rf"^({re.escape(key)}\s*=).*$",
            rf"\g<1>{value}",
            text,
            flags=re.MULTILINE,
        )
        conf_path.write_text(new_text)
    except Exception as exc:
        log.warning("could not update %s in conf: %s", key, exc)
