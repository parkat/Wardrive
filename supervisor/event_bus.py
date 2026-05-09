"""
Async event bus — broadcasts structured events to all subscribers.
Subscribers are coroutines that receive (event_type, payload).
Dead/slow subscribers are dropped after SEND_TIMEOUT seconds.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)

SEND_TIMEOUT = 2.0


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)


# Subscriber = async callable that receives an Event
Subscriber = Callable[[Event], Coroutine]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []
        self._lock = asyncio.Lock()
        # Persistent log for recent events (for /api/debug/events)
        self._history: list[Event] = []
        self._history_max = 500

    async def subscribe(self, fn: Subscriber) -> None:
        async with self._lock:
            self._subscribers.append(fn)

    async def unsubscribe(self, fn: Subscriber) -> None:
        async with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not fn]

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = Event(type=event_type, data=data or {})
        async with self._lock:
            subscribers = list(self._subscribers)
            self._history.append(event)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max :]

        dead: list[Subscriber] = []
        for fn in subscribers:
            try:
                await asyncio.wait_for(fn(event), timeout=SEND_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("event subscriber timed out, dropping: %s", fn)
                dead.append(fn)
            except Exception as exc:
                log.warning("event subscriber error (%s): %s", fn, exc)
                dead.append(fn)

        if dead:
            async with self._lock:
                self._subscribers = [s for s in self._subscribers if s not in dead]

    def recent_events(self, limit: int = 200) -> list[dict]:
        events = self._history[-limit:]
        return [{"type": e.type, "data": e.data, "ts": e.ts} for e in events]


# Module-level singleton shared between supervisor and webapp
bus = EventBus()
