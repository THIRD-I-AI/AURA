"""
Window Processor
=================
Implements temporal windowing with real streaming semantics:
  - Tumbling windows:  fixed-size, non-overlapping
  - Sliding windows:   fixed-size, overlapping (slides by interval)
  - Session windows:   groups events by activity gap
  - Global window:     single window across all time

Features:
  - Event-time assignment (uses event timestamp, not wall clock)
  - Watermark tracking (estimates completeness)
  - Late data detection & configurable handling (drop / update / dead_letter)
  - Per-key window state with aggregation accumulation
  - Window triggering on watermark advancement
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from pipeline.streaming.models import (
    LateDataPolicy,
    StreamEvent,
    WindowConfig,
    WindowState,
    WindowType,
)


def _window_key(event_key: Optional[str], window_start: float, window_end: float) -> str:
    """Unique identifier for a window instance."""
    return f"{event_key or '__global__'}|{window_start:.0f}-{window_end:.0f}"


class WindowProcessor:
    """
    Stateful window processor with event-time semantics.

    Lifecycle:
      1. assign_windows(event)      → determine which windows the event belongs to
      2. accumulate(event, windows)  → update window state with event data
      3. advance_watermark(ts)       → close windows behind the watermark
      4. fire_windows()              → emit results from closed windows
    """

    def __init__(
        self,
        config: WindowConfig,
        watermark_delay: float = 10.0,
        aggregate_fields: Optional[List[Dict[str, str]]] = None,
    ):
        self.config = config
        self.watermark_delay = watermark_delay
        self.aggregate_fields = aggregate_fields or []

        # State: keyed_window_id → WindowState
        self._windows: Dict[str, WindowState] = {}
        self._watermark: float = 0.0
        self._closed_windows: List[WindowState] = []

        # Metrics
        self.late_events = 0
        self.total_events = 0

    @property
    def watermark(self) -> float:
        return self._watermark

    @property
    def active_window_count(self) -> int:
        return sum(1 for w in self._windows.values() if not w.is_closed)

    @property
    def closed_window_count(self) -> int:
        return len(self._closed_windows)

    # ── Main Processing Pipeline ──────────────────────────────────

    def process_event(self, event: StreamEvent) -> Tuple[List[WindowState], List[StreamEvent]]:
        """
        Process a single event through the windowing logic.

        Returns:
          - fired: list of window states that are now complete and ready to emit
          - late_events: list of events that arrived after the watermark
        """
        self.total_events += 1
        late: List[StreamEvent] = []

        # Check if event is late
        if event.timestamp < self._watermark:
            event.is_late = True
            self.late_events += 1

            if self.config.late_data_policy == LateDataPolicy.DROP:
                return [], [event]
            elif self.config.late_data_policy == LateDataPolicy.DEAD_LETTER:
                return [], [event]
            # LateDataPolicy.UPDATE falls through to normal processing

        # Determine windows for this event
        windows = self._assign_windows(event)

        # Accumulate into each window
        for win_key in windows:
            self._accumulate(event, win_key)

        # Advance watermark
        self._advance_watermark(event.timestamp)

        # Check for fired windows
        fired = self._fire_ready_windows()

        return fired, late

    def process_batch(
        self, events: List[StreamEvent]
    ) -> Tuple[List[WindowState], List[StreamEvent]]:
        """Process a batch of events. Returns all fired windows and late events."""
        all_fired: List[WindowState] = []
        all_late: List[StreamEvent] = []

        for event in events:
            fired, late = self.process_event(event)
            all_fired.extend(fired)
            all_late.extend(late)

        return all_fired, all_late

    # ── Window Assignment ─────────────────────────────────────────

    def _assign_windows(self, event: StreamEvent) -> List[str]:
        """Determine which window(s) this event belongs to."""
        et = event.timestamp
        wtype = self.config.type

        if wtype == WindowType.TUMBLING:
            return self._assign_tumbling(event.key, et)
        elif wtype == WindowType.SLIDING:
            return self._assign_sliding(event.key, et)
        elif wtype == WindowType.SESSION:
            return self._assign_session(event.key, et)
        elif wtype == WindowType.GLOBAL:
            return self._assign_global(event.key)
        return []

    def _assign_tumbling(self, key: Optional[str], et: float) -> List[str]:
        """Tumbling: each event belongs to exactly one window."""
        size = self.config.size_seconds
        window_start = math.floor(et / size) * size
        window_end = window_start + size
        win_key = _window_key(key, window_start, window_end)

        if win_key not in self._windows:
            self._windows[win_key] = WindowState(
                window_key=win_key,
                window_start=window_start,
                window_end=window_end,
            )
        return [win_key]

    def _assign_sliding(self, key: Optional[str], et: float) -> List[str]:
        """Sliding: an event can belong to multiple overlapping windows."""
        size = self.config.size_seconds
        slide = self.config.slide_seconds or size  # fallback to tumbling
        keys: List[str] = []

        # Find all windows that contain this event time
        earliest_start = math.floor((et - size) / slide) * slide + slide
        latest_start = math.floor(et / slide) * slide

        start = earliest_start
        while start <= latest_start:
            end = start + size
            if start <= et < end:
                win_key = _window_key(key, start, end)
                if win_key not in self._windows:
                    self._windows[win_key] = WindowState(
                        window_key=win_key,
                        window_start=start,
                        window_end=end,
                    )
                keys.append(win_key)
            start += slide

        return keys

    def _assign_session(self, key: Optional[str], et: float) -> List[str]:
        """Session: group events that are within a gap of each other."""
        gap = self.config.gap_seconds or 30
        k = key or "__global__"

        # Find an existing session for this key
        for win_key, ws in self._windows.items():
            if ws.is_closed:
                continue
            if not win_key.startswith(f"{k}|"):
                continue
            # Check if event falls within the session + gap
            if ws.window_start - gap <= et <= ws.last_event_time + gap:
                # Extend the session
                ws.window_end = max(ws.window_end, et + gap)
                return [win_key]

        # No matching session → create new one
        win_key = _window_key(k, et, et + gap)
        self._windows[win_key] = WindowState(
            window_key=win_key,
            window_start=et,
            window_end=et + gap,
        )
        return [win_key]

    def _assign_global(self, key: Optional[str]) -> List[str]:
        """Global: single window for all time."""
        k = key or "__global__"
        win_key = f"{k}|global"
        if win_key not in self._windows:
            self._windows[win_key] = WindowState(
                window_key=win_key,
                window_start=0.0,
                window_end=float("inf"),
            )
        return [win_key]

    # ── Accumulation ──────────────────────────────────────────────

    def _accumulate(self, event: StreamEvent, win_key: str) -> None:
        """Add event data to the window's aggregation state."""
        ws = self._windows[win_key]
        ws.event_count += 1
        ws.last_event_time = max(ws.last_event_time, event.timestamp)

        # Update aggregations based on configured fields
        if self.aggregate_fields:
            for agg in self.aggregate_fields:
                func = agg.get("function", "COUNT").upper()
                col = agg.get("column", "*")
                alias = agg.get("alias", f"{func}_{col}")

                val = event.data.get(col) if col != "*" else 1
                if val is None:
                    continue
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    continue

                if func == "COUNT":
                    ws.aggregations[alias] = ws.aggregations.get(alias, 0) + 1
                elif func == "SUM":
                    ws.aggregations[alias] = ws.aggregations.get(alias, 0) + val
                elif func == "MIN":
                    ws.aggregations[alias] = min(ws.aggregations.get(alias, float("inf")), val)
                elif func == "MAX":
                    ws.aggregations[alias] = max(ws.aggregations.get(alias, float("-inf")), val)
                elif func == "AVG":
                    # Store running sum and count, compute avg at emission
                    sum_key = f"__{alias}_sum"
                    cnt_key = f"__{alias}_cnt"
                    ws.aggregations[sum_key] = ws.aggregations.get(sum_key, 0) + val
                    ws.aggregations[cnt_key] = ws.aggregations.get(cnt_key, 0) + 1
                    ws.aggregations[alias] = ws.aggregations[sum_key] / ws.aggregations[cnt_key]
        else:
            # Default: just count events
            ws.aggregations["count"] = ws.event_count

    # ── Watermark & Firing ────────────────────────────────────────

    def _advance_watermark(self, event_time: float) -> None:
        """Advance the watermark based on the latest event time."""
        candidate = event_time - self.watermark_delay
        if candidate > self._watermark:
            self._watermark = candidate

    def _fire_ready_windows(self) -> List[WindowState]:
        """Close and return windows that are behind the watermark."""
        fired: List[WindowState] = []

        for win_key, ws in list(self._windows.items()):
            if ws.is_closed:
                continue
            if ws.window_end == float("inf"):
                continue  # global windows don't close

            # Session windows: check if gap has expired
            if self.config.type == WindowType.SESSION:
                gap = self.config.gap_seconds or 30
                if self._watermark > ws.last_event_time + gap:
                    ws.is_closed = True
                    fired.append(ws)
                    self._closed_windows.append(ws)
            else:
                # Tumbling/Sliding: close when watermark passes window end
                if self._watermark >= ws.window_end:
                    ws.is_closed = True
                    fired.append(ws)
                    self._closed_windows.append(ws)

        # Clean up closed windows from active map (keep last 1000 for metrics)
        for ws in fired:
            self._windows.pop(ws.window_key, None)

        if len(self._closed_windows) > 1000:
            self._closed_windows = self._closed_windows[-500:]

        return fired

    # ── Serialisation for Checkpointing ───────────────────────────

    def get_state(self) -> List[WindowState]:
        """Return all active window states for checkpointing."""
        return list(self._windows.values())

    def restore_state(self, states: List[WindowState], watermark: float) -> None:
        """Restore window state from a checkpoint."""
        self._windows.clear()
        for ws in states:
            self._windows[ws.window_key] = ws
        self._watermark = watermark
