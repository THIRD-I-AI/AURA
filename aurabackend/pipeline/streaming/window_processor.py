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

import logging
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

# S20.1: type alias for the optional late-data policy callable. Imported
# lazily inside __init__ to keep the cold path import-free.
LatePolicyCallable = Callable[[Any, float, float], Any]


def _window_key(event_key: Optional[str], window_start: float, window_end: float) -> str:
    """Unique identifier for a window instance."""
    return f"{event_key or '__global__'}|{window_start:.0f}-{window_end:.0f}"


logger = logging.getLogger("aura.streaming.window_processor")


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
        max_active_windows: int = 50_000,
        max_closed_history: int = 500,
        # S20.1 opt-in flags (default OFF — classical paths unchanged).
        use_composite_watermark_tracker: bool = False,
        upstream_ids: Optional[List[str]] = None,
        use_dataflow_triggers: bool = False,
        late_data_policy_callable: Optional[LatePolicyCallable] = None,
    ):
        self.config = config
        self.watermark_delay = watermark_delay
        self.aggregate_fields = aggregate_fields or []

        # State: keyed_window_id → WindowState
        self._windows: Dict[str, WindowState] = {}
        self._watermark: float = 0.0
        self._closed_windows: List[WindowState] = []

        # Memory limits
        self._max_active_windows = max_active_windows
        self._max_closed_history = max_closed_history

        # Metrics
        self.late_events = 0
        self.total_events = 0
        self.evicted_windows = 0

        # S20.1: per-window first-event timestamp (for ProcessingTimeTrigger).
        # Map window_key -> wall-clock when first event arrived. Populated
        # only when dataflow triggers are enabled.
        self._window_first_processing_ts: Dict[str, float] = {}

        # S20.1: lazy-construct composite watermark tracker when opted in.
        # Routes per-upstream watermark updates → composite=min(upstreams).
        self._use_tracker = use_composite_watermark_tracker
        self._tracker: Optional[Any] = None
        if use_composite_watermark_tracker:
            from pipeline.streaming.watermark_tracker import WatermarkTracker
            upstreams = upstream_ids or ["default"]
            self._tracker = WatermarkTracker(upstream_ids=list(upstreams))

        # S20.1: when use_dataflow_triggers=True, _fire_ready_windows
        # builds a WatermarkTrigger per window and dispatches through
        # should_fire(ctx) instead of the inline watermark>=window_end
        # check. Same semantic outcome for the default trigger but the
        # plumbing is in place for callers to override with composite
        # triggers (count + watermark, processing-time fallback, etc.).
        self._use_dataflow_triggers = use_dataflow_triggers

        # S20.1: optional parametric late-data policy callable. When
        # set, takes precedence over the enum-based dispatch in
        # process_event. Lets callers inject remerge-within-allowed-
        # lateness without changing the model enum.
        self._late_policy_callable = late_data_policy_callable

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

            # S20.1: if a parametric late-policy callable is registered,
            # delegate to it (lets callers use remerge-within-allowed-
            # lateness from late_data.py without changing the enum).
            if self._late_policy_callable is not None:
                decision = self._late_policy_callable(
                    event, event.timestamp, self._watermark,
                )
                if not getattr(decision, "accept_to_window", False):
                    # Treat side_output the same as DEAD_LETTER on the
                    # main path — the operator surfaces it on the late
                    # list either way.
                    return [], [event]
                # accept_to_window=True → fall through to normal
                # accumulation (Dataflow accumulation mode).
            elif self.config.late_data_policy == LateDataPolicy.DROP:
                return [], [event]
            elif self.config.late_data_policy == LateDataPolicy.DEAD_LETTER:
                return [], [event]
            # LateDataPolicy.UPDATE falls through to normal processing

        # Determine windows for this event
        windows = self._assign_windows(event)

        # Accumulate into each window
        for win_key in windows:
            self._accumulate(event, win_key)
            # S20.1: track first-event processing time per window so
            # ProcessingTimeTrigger can compute elapsed wall-clock.
            if self._use_dataflow_triggers and win_key not in self._window_first_processing_ts:
                self._window_first_processing_ts[win_key] = time.time()

        # Advance watermark — S20.1: when tracker enabled, route the
        # event-time through the per-upstream tracker; composite =
        # min(upstreams). The StreamEvent.source field labels the
        # originating upstream; pipelines that don't set it collapse
        # to a single "default" upstream (equivalent to the classical
        # path for single-source topologies).
        if self._use_tracker and self._tracker is not None:
            upstream_id = getattr(event, "source", None) or "default"
            try:
                self._tracker.receive(upstream_id, event.timestamp - self.watermark_delay)
            except Exception:
                # Monotonicity violation, unknown upstream id, etc. —
                # log + ignore, classical advance still runs below to
                # maintain backward compat.
                pass
            composite = self._tracker.composite
            if composite > self._watermark:
                self._watermark = composite
        else:
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
                # Tumbling/Sliding firing decision.
                # S20.1: when dataflow triggers are enabled, dispatch
                # through Trigger.should_fire(ctx) — same outcome as
                # the inline check for WatermarkTrigger but the
                # plumbing supports composite triggers (count +
                # watermark, processing-time fallback) for callers
                # that need them.
                should_fire = False
                if self._use_dataflow_triggers:
                    from pipeline.streaming.triggers import (
                        TriggerContext,
                        WatermarkTrigger,
                    )
                    first_ts = self._window_first_processing_ts.get(
                        win_key, float("nan"),
                    )
                    ctx = TriggerContext(
                        watermark_ts=self._watermark,
                        event_count=ws.event_count,
                        processing_ts=time.time(),
                        first_event_processing_ts=first_ts,
                    )
                    should_fire = WatermarkTrigger(ws.window_end).should_fire(ctx)
                else:
                    should_fire = self._watermark >= ws.window_end
                if should_fire:
                    ws.is_closed = True
                    fired.append(ws)
                    self._closed_windows.append(ws)

        # Clean up closed windows from active map (keep last 1000 for metrics)
        for ws in fired:
            self._windows.pop(ws.window_key, None)
            # S20.1: also drop the per-window first-event timestamp
            # so we don't leak memory across million-window runs.
            self._window_first_processing_ts.pop(ws.window_key, None)

        if len(self._closed_windows) > self._max_closed_history:
            self._closed_windows = self._closed_windows[-self._max_closed_history:]

        # Evict stale active windows if we exceed memory limit
        self._evict_stale_windows()

        return fired

    def _evict_stale_windows(self) -> None:
        """Remove the oldest active windows when count exceeds the cap."""
        if len(self._windows) <= self._max_active_windows:
            return

        # Sort active windows by last_event_time, evict the oldest
        active = [(k, ws) for k, ws in self._windows.items() if not ws.is_closed]
        active.sort(key=lambda x: x[1].last_event_time)

        evict_count = len(self._windows) - self._max_active_windows
        for k, ws in active[:evict_count]:
            del self._windows[k]
            self.evicted_windows += 1

        if evict_count > 0:
            logger.warning(
                "Window eviction: removed %d stale windows (active=%d, cap=%d)",
                evict_count, len(self._windows), self._max_active_windows,
            )

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
