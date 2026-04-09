"""
Backpressure Manager
=====================
Flow control between streaming pipeline stages to prevent
data loss when sinks are slower than sources.

Strategies:
  - BLOCK:     pause source reads when buffer is full (default, safest)
  - DROP_TAIL: drop newest events when buffer is full
  - SAMPLE:    accept every Nth event under pressure

Monitors internal buffer depth and exposes backpressure state for
metrics reporting.
"""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import List, Optional

from pipeline.streaming.models import StreamEvent

logger = logging.getLogger("aura.streaming.backpressure")


class BackpressureStrategy(str, Enum):
    BLOCK = "block"
    DROP_TAIL = "drop_tail"
    SAMPLE = "sample"


class BackpressureManager:
    """
    Bounded async buffer between source and processing stages
    with configurable overflow strategy.
    """

    def __init__(
        self,
        max_buffer_size: int = 10_000,
        strategy: BackpressureStrategy = BackpressureStrategy.BLOCK,
        sample_rate: int = 2,
        high_watermark: float = 0.8,
        low_watermark: float = 0.5,
    ):
        self._max_size = max_buffer_size
        self._strategy = strategy
        self._sample_rate = max(1, sample_rate)
        self._high_wm = high_watermark
        self._low_wm = low_watermark

        self._buffer: asyncio.Queue[Optional[StreamEvent]] = asyncio.Queue(maxsize=max_buffer_size)
        self._event_count = 0
        self._dropped_count = 0
        self._is_pressured = False
        self._pressure_start: float = 0.0
        self._total_pressure_seconds: float = 0.0

    @property
    def buffer_depth(self) -> int:
        return self._buffer.qsize()

    @property
    def buffer_utilization(self) -> float:
        return self._buffer.qsize() / max(self._max_size, 1)

    @property
    def is_pressured(self) -> bool:
        return self._is_pressured

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def total_pressure_seconds(self) -> float:
        extra = 0.0
        if self._is_pressured and self._pressure_start > 0:
            extra = time.time() - self._pressure_start
        return self._total_pressure_seconds + extra

    async def put_batch(self, events: List[StreamEvent]) -> int:
        """
        Add events to the buffer, applying backpressure strategy.
        Returns the number of events actually enqueued.
        """
        enqueued = 0
        for event in events:
            self._event_count += 1
            accepted = await self._put_one(event)
            if accepted:
                enqueued += 1

        self._update_pressure_state()
        return enqueued

    async def get_batch(self, max_events: int = 100, timeout: float = 0.5) -> List[StreamEvent]:
        """
        Drain up to max_events from the buffer.
        Returns as many events as are available within timeout.
        """
        events: List[StreamEvent] = []
        try:
            # Block for the first event up to timeout
            event = await asyncio.wait_for(self._buffer.get(), timeout=timeout)
            if event is not None:
                events.append(event)
        except asyncio.TimeoutError:
            return events

        # Drain remaining without blocking
        while len(events) < max_events:
            try:
                event = self._buffer.get_nowait()
                if event is None:
                    break
                events.append(event)
            except asyncio.QueueEmpty:
                break

        self._update_pressure_state()
        return events

    async def flush(self) -> None:
        """Signal shutdown — put sentinel to unblock get_batch."""
        try:
            self._buffer.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def _put_one(self, event: StreamEvent) -> bool:
        """Enqueue a single event using the configured strategy."""
        if self._strategy == BackpressureStrategy.BLOCK:
            try:
                await asyncio.wait_for(self._buffer.put(event), timeout=5.0)
                return True
            except asyncio.TimeoutError:
                self._dropped_count += 1
                logger.warning("Backpressure BLOCK timeout — dropped event")
                return False

        elif self._strategy == BackpressureStrategy.DROP_TAIL:
            try:
                self._buffer.put_nowait(event)
                return True
            except asyncio.QueueFull:
                self._dropped_count += 1
                return False

        elif self._strategy == BackpressureStrategy.SAMPLE:
            if self._is_pressured and (self._event_count % self._sample_rate != 0):
                self._dropped_count += 1
                return False
            try:
                self._buffer.put_nowait(event)
                return True
            except asyncio.QueueFull:
                self._dropped_count += 1
                return False

        return False

    def _update_pressure_state(self) -> None:
        """Update pressure state based on buffer utilization with hysteresis."""
        util = self.buffer_utilization
        if not self._is_pressured and util >= self._high_wm:
            self._is_pressured = True
            self._pressure_start = time.time()
            logger.warning(
                "Backpressure ENGAGED: buffer %.0f%% full (%d/%d)",
                util * 100, self.buffer_depth, self._max_size,
            )
        elif self._is_pressured and util <= self._low_wm:
            self._is_pressured = False
            if self._pressure_start > 0:
                self._total_pressure_seconds += time.time() - self._pressure_start
            self._pressure_start = 0.0
            logger.info(
                "Backpressure RELEASED: buffer %.0f%% full",
                util * 100,
            )

    def stats(self) -> dict:
        """Return current backpressure statistics."""
        return {
            "buffer_depth": self.buffer_depth,
            "buffer_utilization": round(self.buffer_utilization, 3),
            "is_pressured": self._is_pressured,
            "dropped_count": self._dropped_count,
            "total_pressure_seconds": round(self.total_pressure_seconds, 2),
            "strategy": self._strategy.value,
        }
