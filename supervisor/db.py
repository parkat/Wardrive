"""
Async SQLite interface for the supervisor.
Opens a new connection per call to avoid threading issues.
WAL mode + 10s busy_timeout for robustness.
"""

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SupervisorDB:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    async def _run(self, fn) -> Any:
        """Run a synchronous DB operation in the executor (non-blocking)."""
        loop = asyncio.get_event_loop()
        async with self._lock:
            return await loop.run_in_executor(None, fn)

    async def log_event(self, collector_id: str, event_type: str, details: dict) -> None:
        details_json = json.dumps(details)
        ts = time.time()

        def _write():
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO collector_events (ts, collector_id, event_type, details) "
                    "VALUES (?, ?, ?, ?)",
                    (ts, collector_id, event_type, details_json),
                )
        try:
            await self._run(_write)
        except Exception as exc:
            log.warning("db log_event failed: %s", exc)

    async def log_power_event(self, port: str, milliamps: int | None, event_type: str) -> None:
        ts = time.time()

        def _write():
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO power_events (ts, port, milliamps, event_type) "
                    "VALUES (?, ?, ?, ?)",
                    (ts, port, milliamps, event_type),
                )
        try:
            await self._run(_write)
        except Exception as exc:
            log.warning("db log_power_event failed: %s", exc)

    async def recent_events(self, limit: int = 200) -> list[dict]:
        def _read():
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT ts, collector_id AS source, event_type, details
                    FROM collector_events
                    UNION ALL
                    SELECT ts, port AS source, event_type, NULL AS details
                    FROM power_events
                    ORDER BY ts DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        try:
            return await self._run(_read)
        except Exception as exc:
            log.warning("db recent_events failed: %s", exc)
            return []

    async def table_stats(self) -> dict:
        def _read():
            tables = [
                "sessions", "bt_devices", "bt_obs",
                "wifi_aps", "wifi_obs", "wifi_clients",
                "rf_devices", "rf_obs",
                "collector_events", "power_events",
            ]
            stats: dict[str, Any] = {}
            with self._connect() as conn:
                for t in tables:
                    try:
                        row = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
                        stats[t] = {"count": row["n"]}
                    except sqlite3.OperationalError:
                        stats[t] = {"count": None, "error": "table missing"}
                wal = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
                stats["_wal"] = {"log": wal[1], "checkpointed": wal[2]}
                import os
                stats["_size_bytes"] = os.path.getsize(str(self._db_path))
            return stats
        try:
            return await self._run(_read)
        except Exception as exc:
            return {"error": str(exc)}

    async def vacuum(self) -> None:
        def _vac():
            # WAL mode: checkpoint first, then vacuum
            conn = self._connect()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
            conn.close()
        await self._run(_vac)
