"""
WebSocket broadcast hub — bridges the supervisor event_bus to connected browsers.
Handles slow/dead clients gracefully: they are dropped without affecting others.
"""

import asyncio
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

SEND_TIMEOUT = 3.0
PING_INTERVAL = 20.0   # keep connections alive through proxies


class WSHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        log.debug("ws client connected (total=%d)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
        log.debug("ws client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, msg: dict) -> None:
        text = json.dumps(msg)
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await asyncio.wait_for(ws.send_text(text), timeout=SEND_TIMEOUT)
            except (asyncio.TimeoutError, WebSocketDisconnect, Exception):
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def serve(self, ws: WebSocket) -> None:
        """Main WebSocket handler — subscribe, ping-loop, clean up on disconnect."""
        await self.connect(ws)
        try:
            while True:
                try:
                    # recv_text with timeout; use the timeout to send pings
                    await asyncio.wait_for(ws.receive_text(), timeout=PING_INTERVAL)
                except asyncio.TimeoutError:
                    # Client alive check
                    try:
                        await asyncio.wait_for(
                            ws.send_text(json.dumps({"type": "ping", "ts": time.time()})),
                            timeout=SEND_TIMEOUT,
                        )
                    except Exception:
                        break
                except WebSocketDisconnect:
                    break
                except Exception:
                    break
        finally:
            await self.disconnect(ws)


# Singleton used by both routes and the event_bus subscriber
hub = WSHub()
debug_hub = WSHub()


async def _forward_to_browsers(event) -> None:
    """Subscribed to the supervisor event_bus; forwards all events to browser clients."""
    await hub.broadcast({"type": event.type, "data": event.data, "ts": event.ts})
    await debug_hub.broadcast({"type": event.type, "data": event.data, "ts": event.ts})


async def start_forwarding() -> None:
    """Call once at app startup to wire the event_bus → WebSocket broadcast."""
    try:
        from supervisor.event_bus import bus
        await bus.subscribe(_forward_to_browsers)
    except ImportError:
        log.warning("supervisor event_bus not available — WebSocket events disabled")
