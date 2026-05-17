"""WebSocket pub/sub fan-out used by the streaming endpoints.

The bus is intentionally tiny: it keeps a per-channel asyncio queue for
each subscriber and drops messages on slow consumers rather than blocking
the producers. This keeps the SIGINT/heatmap workers from stalling when an
operator tablet falls behind.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


log = logging.getLogger(__name__)


class StreamBus:
    """In-process pub/sub. Each channel can have many subscribers."""

    def __init__(self, *, max_queue: int = 64) -> None:
        self._max_queue = max_queue
        self._channels: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, channel: str, payload: Any) -> int:
        async with self._lock:
            subscribers = list(self._channels.get(channel, ()))
        delivered = 0
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                log.debug("dropping payload for slow subscriber on channel %s", channel)
        return delivered

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._channels[channel].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subs = self._channels.get(channel)
                if subs is not None:
                    subs.discard(queue)
                    if not subs:
                        self._channels.pop(channel, None)

    def subscriber_count(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))
