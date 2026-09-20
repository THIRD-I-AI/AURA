"""
File Watcher Source Adapter
=============================
Watches a directory for new files and emits each row as a StreamEvent.
Supports CSV, JSON, and Parquet files.

Config options:
  watch_dir:            str   – directory to watch (default: data/uploads)
  pattern:              str   – glob pattern (default: "*.csv")
  poll_interval_seconds: float – how often to scan (default: 2.0)
  max_seen_files:       int   – cap on remembered filenames (default: 10000)
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Set

from pipeline.streaming.models import StreamEvent
from pipeline.streaming.sources.base import BaseSource

_DEFAULT_MAX_SEEN_FILES = 10000


class FileWatcherSource(BaseSource):
    """Watches a directory and emits file rows as streaming events."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.watch_dir: str = config.get("watch_dir", "data/uploads")
        self.pattern: str = config.get("pattern", "*.csv")
        self.poll_interval: float = config.get("poll_interval_seconds", 2.0)
        # BUG-130: an unbounded set grows for the life of the pipeline (one
        # entry per file ever observed) and is serialized whole into every
        # periodic checkpoint -- mirror BUG-091's AlertSink._fired bound:
        # a set for O(1) membership plus an insertion-order deque so the
        # OLDEST filename (not an arbitrary one) is evicted once the cap
        # is hit.
        self._max_seen_files = int(config.get("max_seen_files", _DEFAULT_MAX_SEEN_FILES))
        self._seen_files: Set[str] = set()
        self._seen_files_order: Deque[str] = deque()
        self._pending_events: List[StreamEvent] = []
        self._event_count = 0

    def _mark_seen(self, fstr: str) -> None:
        if fstr in self._seen_files:
            return
        if len(self._seen_files) >= self._max_seen_files:
            oldest = self._seen_files_order.popleft()
            self._seen_files.discard(oldest)
        self._seen_files.add(fstr)
        self._seen_files_order.append(fstr)

    async def start(self) -> None:
        self._running = True
        # Mark existing files as already seen. BUG-124: the directory
        # listing (exists()+glob()) is itself a blocking filesystem call,
        # same as the per-file parse BUG-087 already offloaded -- run it
        # via asyncio.to_thread too, or it blocks the shared event loop for
        # the duration of the scan.
        existing = await asyncio.to_thread(self._scan_dir)
        for fstr in existing:
            self._mark_seen(fstr)

    async def stop(self) -> None:
        self._running = False

    def _scan_dir(self) -> List[str]:
        """Blocking directory listing -- always call via asyncio.to_thread
        (BUG-124), never directly on the event loop."""
        watch_path = Path(self.watch_dir)
        if not watch_path.exists():
            return []
        return [str(f) for f in watch_path.glob(self.pattern)]

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self._running:
            return []

        # If we have pending events from a previous file scan, drain them
        if self._pending_events:
            batch = self._pending_events[:max_events]
            self._pending_events = self._pending_events[max_events:]
            return batch

        # Scan for new files. BUG-124: runs every poll cycle (default 2s)
        # for every running file-watcher pipeline -- offload it, not just
        # the per-file parse below.
        found = await asyncio.to_thread(self._scan_dir)
        new_files: List[Path] = []
        for fstr in found:
            if fstr not in self._seen_files:
                new_files.append(Path(fstr))
                self._mark_seen(fstr)

        # Parse new files into events. BUG-087: _parse_csv/_parse_json/
        # _parse_parquet do blocking file I/O (and, for Parquet, CPU-bound
        # parsing) -- running them directly on the event loop freezes every
        # other tenant's concurrent request under the single-worker
        # deployment for the duration of the parse.
        for file_path in new_files:
            events = await asyncio.to_thread(self._parse_file, file_path)
            self._pending_events.extend(events)

        if not self._pending_events:
            await asyncio.sleep(self.poll_interval)
            return []

        batch = self._pending_events[:max_events]
        self._pending_events = self._pending_events[max_events:]
        return batch

    def get_offsets(self) -> Dict[str, Any]:
        return {
            "seen_files": list(self._seen_files),
            "event_count": self._event_count,
            "pending_count": len(self._pending_events),
        }

    # ── Internal parsing ──────────────────────────────────────────

    def _parse_file(self, file_path: Path) -> List[StreamEvent]:
        """Parse a file into a list of StreamEvents."""
        ext = file_path.suffix.lower()
        if ext == ".csv":
            return self._parse_csv(file_path)
        elif ext == ".json":
            return self._parse_json(file_path)
        elif ext == ".parquet":
            return self._parse_parquet(file_path)
        return []

    def _parse_csv(self, file_path: Path) -> List[StreamEvent]:
        events: List[StreamEvent] = []
        try:
            with open(file_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    self._event_count += 1
                    events.append(StreamEvent(
                        timestamp=time.time(),
                        key=file_path.stem,
                        data=dict(row),
                        source=f"file://{file_path.name}",
                    ))
        except Exception:
            pass
        return events

    def _parse_json(self, file_path: Path) -> List[StreamEvent]:
        events: List[StreamEvent] = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = json.load(f)
            rows = content if isinstance(content, list) else [content]
            for row in rows:
                if isinstance(row, dict):
                    self._event_count += 1
                    events.append(StreamEvent(
                        timestamp=time.time(),
                        key=file_path.stem,
                        data=row,
                        source=f"file://{file_path.name}",
                    ))
        except Exception:
            pass
        return events

    def _parse_parquet(self, file_path: Path) -> List[StreamEvent]:
        events: List[StreamEvent] = []
        try:
            import pyarrow.parquet as pq
            rows = pq.read_table(file_path).to_pylist()
            for row in rows:
                self._event_count += 1
                events.append(StreamEvent(
                    timestamp=time.time(),
                    key=file_path.stem,
                    data=row,
                    source=f"file://{file_path.name}",
                ))
        except Exception:
            pass
        return events
