"""
Collector plugin base — defines the data contract every collector must satisfy.
Each collector is an instance of CollectorPlugin registered in the registry.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class HealthState(str, Enum):
    STARTING    = "starting"
    RUNNING     = "running"
    CRASHED     = "crashed"
    DISABLED    = "disabled"
    UNAVAILABLE = "unavailable"


@dataclass
class CollectorPlugin:
    # ── Identity ────────────────────────────────────────────────────────────────
    id: str
    name: str
    type: str          # kismet | serial | sdr | gps | hackrf

    # ── Hardware matching ────────────────────────────────────────────────────────
    # Regex matched against /dev/* paths from udev ADD events.
    # Use "" for collectors that don't map to a single /dev node (e.g., Kismet uses iface).
    device_pattern: str

    # ── Resource / scheduling ────────────────────────────────────────────────────
    power_ma: int       # estimated USB current draw
    priority: int       # lower = higher priority; used for shedding

    # ── Mutex group — collectors sharing a physical device ───────────────────────
    # Only one collector in the same mutex_group runs at a time.
    mutex_group: Optional[str] = None

    # ── Runtime state (managed by supervisor, not set in constructor) ────────────
    state:          HealthState       = field(default=HealthState.UNAVAILABLE, init=False)
    restart_count:  int               = field(default=0, init=False)
    pid:            Optional[int]     = field(default=None, init=False)
    device_path:    Optional[str]     = field(default=None, init=False)
    last_exit_code: Optional[int]     = field(default=None, init=False)
    last_error:     Optional[str]     = field(default=None, init=False)
    started_at:     Optional[float]   = field(default=None, init=False)
    _process:       Optional[asyncio.subprocess.Process] = field(
        default=None, init=False, repr=False
    )
    _last_output_ts: float = field(default_factory=time.monotonic, init=False, repr=False)

    def uptime(self) -> Optional[float]:
        if self.started_at and self.state == HealthState.RUNNING:
            return time.time() - self.started_at
        return None

    def seconds_since_output(self) -> float:
        return time.monotonic() - self._last_output_ts

    def touch_output(self) -> None:
        self._last_output_ts = time.monotonic()

    async def build_command(self, session_dir: Path, cfg: dict) -> list[str]:
        """
        Return the argv list to launch this collector.
        Override in subclasses. session_dir is the active capture session path.
        Raises NotImplementedError if the collector is not ready to launch.
        """
        raise NotImplementedError(f"{self.id}.build_command not implemented")

    async def pre_launch(self, session_dir: Path, cfg: dict) -> None:
        """Optional hook called before the process is started (e.g., set up monitor iface)."""

    async def post_stop(self, cfg: dict) -> None:
        """Optional hook called after the process exits (e.g., tear down monitor iface)."""

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "name":           self.name,
            "type":           self.type,
            "state":          self.state.value,
            "priority":       self.priority,
            "power_ma":       self.power_ma,
            "mutex_group":    self.mutex_group,
            "device_path":    self.device_path,
            "pid":            self.pid,
            "restart_count":  self.restart_count,
            "last_exit_code": self.last_exit_code,
            "last_error":     self.last_error,
            "uptime":         self.uptime(),
        }
