"""
/api/debug — diagnostic and control endpoints for unattended operation.
All routes require Bearer token from wardrive.conf DEBUG_TOKEN.
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug"])
_bearer = HTTPBearer()


def _get_token() -> str:
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from supervisor import config as cfg
        cfg.load()
        return cfg.get("DEBUG_TOKEN", "")
    except Exception:
        return ""


def _require_token(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> None:
    token = _get_token()
    if not token or creds.credentials != token:
        raise HTTPException(403, "invalid or missing debug token")


def _get_registry():
    try:
        from supervisor.registry import Registry
        # Registry is accessed via the app state set at startup
        import webapp.main as app_module
        return getattr(app_module, "_registry", None)
    except Exception:
        return None


def _get_db():
    try:
        import webapp.main as app_module
        return getattr(app_module, "_supervisor_db", None)
    except Exception:
        return None


# ── Collector endpoints ───────────────────────────────────────────────────────

@router.get("/collectors", dependencies=[Depends(_require_token)])
async def debug_collectors():
    registry = _get_registry()
    if not registry:
        raise HTTPException(503, "supervisor registry not available")
    return [c.to_dict() for c in registry.collectors.values()]


@router.get("/collector/{cid}/log", dependencies=[Depends(_require_token)])
async def debug_collector_log(cid: str, lines: int = 100):
    registry = _get_registry()
    if not registry or cid not in registry.collectors:
        raise HTTPException(404, "collector not found")
    # Find the most recent log file for this collector
    from supervisor import config as cfg
    capture_root = cfg.resolve_capture_root()
    logs = sorted(capture_root.glob(f"raw/*/logs/{cid}.log"), reverse=True)
    if not logs:
        return {"lines": [], "log_file": None}
    log_file = logs[0]
    try:
        proc = await asyncio.create_subprocess_exec(
            "tail", "-n", str(lines), str(log_file),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"lines": stdout.decode(errors="replace").splitlines(), "log_file": str(log_file)}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/collector/{cid}/restart", dependencies=[Depends(_require_token)])
async def debug_restart_collector(cid: str):
    registry = _get_registry()
    if not registry or cid not in registry.collectors:
        raise HTTPException(404, "collector not found")
    await registry.restart_collector(cid)
    return {"ok": True}


@router.post("/collector/{cid}/disable", dependencies=[Depends(_require_token)])
async def debug_disable_collector(cid: str):
    registry = _get_registry()
    if not registry or cid not in registry.collectors:
        raise HTTPException(404, "collector not found")
    await registry.disable_collector(cid, reason="api_debug")
    return {"ok": True}


@router.post("/collector/{cid}/enable", dependencies=[Depends(_require_token)])
async def debug_enable_collector(cid: str):
    registry = _get_registry()
    if not registry or cid not in registry.collectors:
        raise HTTPException(404, "collector not found")
    await registry.enable_collector(cid)
    return {"ok": True}


# ── SDR mode toggle ───────────────────────────────────────────────────────────

@router.post("/sdr/mode/{mode}", dependencies=[Depends(_require_token)])
async def debug_set_sdr_mode(mode: str):
    if mode not in ("rtl433", "wideband"):
        raise HTTPException(400, "mode must be rtl433 or wideband")
    registry = _get_registry()
    if not registry:
        raise HTTPException(503, "supervisor not available")
    await registry.set_sdr_mode(mode)
    return {"ok": True, "mode": mode}


# ── USB endpoints ─────────────────────────────────────────────────────────────

@router.get("/usb", dependencies=[Depends(_require_token)])
async def debug_usb():
    from supervisor.power import query_usb_state
    return await query_usb_state()


@router.post("/usb/port/{port}/cycle", dependencies=[Depends(_require_token)])
async def debug_cycle_port(port: str):
    from supervisor.power import cycle_port
    ok = await cycle_port(port)
    return {"ok": ok, "port": port}


# ── Database endpoints ────────────────────────────────────────────────────────

@router.get("/db/health", dependencies=[Depends(_require_token)])
async def debug_db_health():
    db = _get_db()
    if not db:
        raise HTTPException(503, "database not available")
    return await db.table_stats()


@router.post("/db/vacuum", dependencies=[Depends(_require_token)])
async def debug_db_vacuum():
    db = _get_db()
    if not db:
        raise HTTPException(503, "database not available")
    await db.vacuum()
    return {"ok": True}


# ── System info ───────────────────────────────────────────────────────────────

@router.get("/system", dependencies=[Depends(_require_token)])
async def debug_system():
    info: dict = {
        "hostname": platform.node(),
        "uptime_s": _uptime(),
        "cpu_temp_c": _cpu_temp(),
        "memory":  _memory(),
        "disk":    _disk(),
        "dmesg_tail": await _dmesg_tail(50),
    }
    return info


@router.get("/events", dependencies=[Depends(_require_token)])
async def debug_events(limit: int = 200):
    db = _get_db()
    if not db:
        return []
    return await db.recent_events(limit=limit)


# ── Debug WebSocket (streams all events in real-time) ────────────────────────

@router.websocket("/ws")
async def debug_ws(websocket: WebSocket, token: str = ""):
    expected = _get_token()
    if not expected or token != expected:
        await websocket.close(code=4003)
        return
    from webapp.ws.events import debug_hub
    await debug_hub.serve(websocket)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uptime() -> float:
    try:
        return time.time() - os.stat("/proc/1").st_ctime
    except Exception:
        return -1


def _cpu_temp() -> float | None:
    thermal = Path("/sys/class/thermal/thermal_zone0/temp")
    try:
        return int(thermal.read_text()) / 1000.0
    except Exception:
        return None


def _memory() -> dict:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return {"total_mb": vm.total // 1024**2, "used_mb": vm.used // 1024**2,
                "percent": vm.percent}
    except ImportError:
        pass
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        info = {}
        for line in lines:
            k, _, v = line.partition(":")
            info[k.strip()] = v.strip()
        total = int(info.get("MemTotal", "0 kB").split()[0])
        avail = int(info.get("MemAvailable", "0 kB").split()[0])
        return {"total_mb": total // 1024, "available_mb": avail // 1024,
                "used_mb": (total - avail) // 1024}
    except Exception:
        return {}


def _disk() -> dict:
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free  = st.f_bfree  * st.f_frsize
        return {"total_gb": round(total / 1024**3, 1),
                "free_gb":  round(free  / 1024**3, 1),
                "used_pct": round((total - free) / total * 100, 1)}
    except Exception:
        return {}


async def _dmesg_tail(n: int) -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "dmesg", "--time-format=reltime", f"--level=err,warn",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        lines = stdout.decode(errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []
