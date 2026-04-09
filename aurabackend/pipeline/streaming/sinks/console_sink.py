"""
Console Sink – Log window results to stdout / logger
=====================================================
Useful for debugging and development.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from pipeline.streaming.models import WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.console")


class ConsoleSink(BaseSink):

    async def start(self) -> None:
        self._running = True
        logger.info("Console sink started")

    async def stop(self) -> None:
        self._running = False
        logger.info("Console sink stopped")

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        msg = (
            f"[{pipeline_id}] Window closed  "
            f"key={window.window_key}  "
            f"start={window.window_start}  end={window.window_end}  "
            f"events={window.event_count}  "
            f"agg={json.dumps(window.aggregations, default=str)}"
        )
        logger.info(msg)
