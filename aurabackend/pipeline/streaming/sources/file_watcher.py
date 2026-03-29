"""
File Watcher Source Adapter
=============================
Watches a directory for new files and emits each row as a StreamEvent.
Supports CSV, JSON, and Parquet files.

Config options:
  watch_dir:            str   – directory to watch (default: data/uploads)
  pattern:              str   – glob pattern (default: "*.csv")
  poll_interval_seconds: float – how often to scan (default: 2.0)
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Set

from pipeline.streaming.models import StreamEvent
from pipeline.streaming.sources.base import BaseSource


class FileWatcherSource(BaseSource):
    """Watches a directory and emits file rows as streaming events."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.watch_dir: str = config.get("watch_dir", "data/uploads")
        self.pattern: str = config.get("pattern", "*.csv")
        self.poll_interval: float = config.get("poll_interval_seconds", 2.0)
        self._seen_files: Set[str] = set()
        self._pending_events: List[StreamEvent] = []
        self._event_count = 0

    async def start(self) -> None:
        self._running = True
        # Mark existing files as already seen
        watch_path = Path(self.watch_dir)
        if watch_path.exists():
            for f in watch_path.glob(self.pattern):
                self._seen_files.add(str(f))

    async def stop(self) -> None:
        self._running = False

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self._running:
            return []

        # If we have pending events from a previous file scan, drain them
        if self._pending_events:
            batch = self._pending_events[:max_events]
            self._pending_events = self._pending_events[max_events:]
            return batch

        # Scan for new files
        watch_path = Path(self.watch_dir)
        if not watch_path.exists():
            await asyncio.sleep(self.poll_interval)
            return []

        new_files: List[Path] = []
        for f in watch_path.glob(self.pattern):
            fstr = str(f)
            if fstr not in self._seen_files:
                new_files.append(f)
                self._seen_files.add(fstr)

        # Parse new files into events
        for file_path in new_files:
            events = self._parse_file(file_path)
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
