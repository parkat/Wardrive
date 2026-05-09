"""
Config loader — parses wardrive.conf into a typed dict.
Re-reads on SIGHUP without restarting the supervisor.
"""

import os
import re
import threading
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_cfg: dict[str, str] = {}

_PROJECT_ROOT = Path(__file__).parent.parent


def _parse_conf(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Strip inline comments
            line = re.sub(r"\s+#.*$", "", line)
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            # Strip surrounding quotes
            val = val.strip().strip('"').strip("'")
            if key:
                result[key] = val
    return result


def load(conf_path: Path | None = None) -> None:
    global _cfg
    if conf_path is None:
        conf_path = _PROJECT_ROOT / "config" / "wardrive.conf"
    with _lock:
        _cfg = _parse_conf(conf_path)


def get(key: str, default: Any = None) -> Any:
    with _lock:
        return _cfg.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    val = get(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes", "on")


def get_int(key: str, default: int = 0) -> int:
    val = get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_float(key: str, default: float = 0.0) -> float:
    val = get(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default


def all_settings() -> dict[str, str]:
    with _lock:
        return dict(_cfg)


def project_root() -> Path:
    return _PROJECT_ROOT


def resolve_capture_root() -> Path:
    """
    Returns the active data root:
    1. USB drive at /media/<label> if present
    2. DATA_FALLBACK_DIR from config
    """
    label = get("USB_DRIVE_LABEL", "WARDRIVE")
    usb_paths = [
        Path(f"/media/{label}"),
        Path(f"/media/pi/{label}"),
        Path(f"/mnt/{label}"),
        Path(f"/run/media/{label}"),
    ]
    for p in usb_paths:
        if p.is_mount():
            wardrive_dir = p / "wardrive"
            wardrive_dir.mkdir(parents=True, exist_ok=True)
            return wardrive_dir

    fallback = Path(get("DATA_FALLBACK_DIR", str(_PROJECT_ROOT / "data")))
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
