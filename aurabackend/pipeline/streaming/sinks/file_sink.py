"""
File Sink – Write window results to CSV / JSON files
=====================================================
Micro-batch file writes for streaming output.
"""
from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

from pipeline.streaming.models import WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.file")


class FileSink(BaseSink):

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._output_dir = config.get("output_dir", "data/streaming_output")
        self._format = config.get("format", "json")  # json | csv
        self._buffer: List[Dict[str, Any]] = []
        self._flush_every = int(config.get("flush_every", 50))

    async def start(self) -> None:
        os.makedirs(self._output_dir, exist_ok=True)
        self._running = True
        logger.info("File sink started (dir=%s, format=%s)", self._output_dir, self._format)

    async def stop(self) -> None:
        self._running = False
        await self._flush()
        logger.info("File sink stopped")

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        row = {
            "pipeline_id": pipeline_id,
            "window_key": window.window_key,
            "window_start": window.window_start,
            "window_end": window.window_end,
            "event_count": window.event_count,
            "aggregations": window.aggregations,
        }
        self._buffer.append(row)
        if len(self._buffer) >= self._flush_every:
            await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        # Swap in a fresh list before awaiting the offloaded write below --
        # once the write is dispatched to a worker thread, the event loop is
        # free to run other coroutines, and emit_window() appending to the
        # SAME list the thread is reading/iterating would be a real race
        # (not just a style concern: CPython list iteration is index-based,
        # so a concurrent append is read mid-iteration, not just ignored).
        buffer, self._buffer = self._buffer, []

        # BUG-086: open()/write, csv.DictWriter, and json.dump are all
        # synchronous, blocking file I/O -- running them directly on the
        # event loop freezes every other tenant's concurrent request under
        # the single-worker deployment, worse on a slow/network-mounted
        # output_dir. Called from both the hot path (emit_window, gated by
        # flush_every) and stop().
        def _write() -> str:
            if self._format == "csv":
                path = os.path.join(self._output_dir, f"batch_{stamp}.csv")
                fieldnames = list(buffer[0].keys())
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in buffer:
                        row["aggregations"] = json.dumps(row["aggregations"], default=str)
                        writer.writerow(row)
            else:
                path = os.path.join(self._output_dir, f"batch_{stamp}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(buffer, f, indent=2, default=str)
            return path

        path = await asyncio.to_thread(_write)

        logger.info("File sink: flushed %d rows -> %s", len(buffer), path)
