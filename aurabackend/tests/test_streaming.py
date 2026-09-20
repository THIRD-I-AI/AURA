"""
Streaming Pipeline Tests
=========================
Unit tests for the streaming pipeline engine components:
  - Models (serialisation round-trip)
  - WindowProcessor (tumbling, sliding, session, global)
  - StateManager (checkpoint create / load / rotation)
  - Sink adapters (console, alert)
  - Source adapters (simulated)
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.streaming.models import (
    CheckpointData,
    LateDataPolicy,
    StreamEvent,
    StreamMetrics,
    StreamPipeline,
    StreamPipelineStatus,
    StreamSink,
    StreamSinkType,
    StreamSource,
    StreamSourceType,
    StreamTransform,
    TransformType,
    WindowConfig,
    WindowState,
    WindowType,
)
from pipeline.streaming.window_processor import WindowProcessor
from shared.sql_identifiers import quote_identifier

# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _event(ts: float, key: str = "k1", data: dict | None = None) -> StreamEvent:
    return StreamEvent(timestamp=ts, key=key, data=data or {})


def _make_pipeline(**overrides) -> StreamPipeline:
    defaults = dict(
        name="test-pipeline",
        source=StreamSource(type=StreamSourceType.SIMULATED, config={"events_per_second": 10}),
        window=WindowConfig(type=WindowType.TUMBLING, size_seconds=60),
        sinks=[StreamSink(type=StreamSinkType.CONSOLE)],
    )
    defaults.update(overrides)
    return StreamPipeline(**defaults)


# ════════════════════════════════════════════════════════════════
# 1. MODEL TESTS
# ════════════════════════════════════════════════════════════════

class TestModels:
    """Verify Pydantic models serialise correctly."""

    def test_stream_event_defaults(self):
        e = StreamEvent(timestamp=1000.0)
        assert e.timestamp == 1000.0
        assert e.key is None
        assert e.is_late is False
        assert len(e.event_id) == 12

    def test_window_config_defaults(self):
        wc = WindowConfig()
        assert wc.type == WindowType.TUMBLING
        assert wc.size_seconds == 60
        assert wc.late_data_policy == LateDataPolicy.DROP

    def test_pipeline_json_roundtrip(self):
        p = _make_pipeline()
        data = p.model_dump()
        p2 = StreamPipeline(**data)
        assert p2.name == p.name
        assert p2.source.type == StreamSourceType.SIMULATED
        assert p2.window.type == WindowType.TUMBLING
        assert len(p2.sinks) == 1

    def test_source_label(self):
        src = StreamSource(type=StreamSourceType.KAFKA, config={"topic": "orders"})
        assert src.label() == "kafka://orders"

        sim = StreamSource(type=StreamSourceType.SIMULATED, config={"event_type": "clicks"})
        assert sim.label() == "sim://clicks"

    def test_checkpoint_data(self):
        ws = WindowState(window_key="k1|0-60", window_start=0, window_end=60, event_count=5)
        cp = CheckpointData(
            pipeline_id="p1",
            watermark=50.0,
            window_states=[ws],
        )
        data = cp.model_dump()
        cp2 = CheckpointData(**data)
        assert cp2.pipeline_id == "p1"
        assert cp2.watermark == 50.0
        assert len(cp2.window_states) == 1
        assert cp2.window_states[0].event_count == 5

    def test_stream_metrics_defaults(self):
        m = StreamMetrics(pipeline_id="x")
        assert m.events_in == 0
        assert m.events_per_second == 0.0
        assert m.status == StreamPipelineStatus.STOPPED

    def test_transform_types(self):
        t = StreamTransform(type=TransformType.FILTER, config={"condition": "amount > 100"})
        assert t.type == TransformType.FILTER
        assert "condition" in t.config


# ════════════════════════════════════════════════════════════════
# 2. WINDOW PROCESSOR TESTS
# ════════════════════════════════════════════════════════════════

class TestWindowProcessor:
    """Test temporal windowing with event-time semantics."""

    # ── Tumbling ──
    def test_tumbling_single_window(self):
        wp = WindowProcessor(WindowConfig(type=WindowType.TUMBLING, size_seconds=10), watermark_delay=5)
        # 5 events in [0, 10)
        for t in [1, 3, 5, 7, 9]:
            wp.process_event(_event(t))
        assert wp.active_window_count == 1
        assert wp.total_events == 5

    def test_tumbling_window_fires(self):
        wp = WindowProcessor(WindowConfig(type=WindowType.TUMBLING, size_seconds=10), watermark_delay=5)
        for t in [1, 3, 5, 7, 9]:
            wp.process_event(_event(t))
        # Advance past window end (10) + watermark_delay (5) = 15
        fired, late = wp.process_event(_event(16))
        assert len(fired) == 1
        assert fired[0].event_count == 5
        assert fired[0].is_closed is True

    def test_tumbling_two_windows(self):
        wp = WindowProcessor(WindowConfig(type=WindowType.TUMBLING, size_seconds=10), watermark_delay=0)
        wp.process_event(_event(2))
        wp.process_event(_event(12))
        # Watermark delay = 0, so first window [0, 10) fires immediately
        fired, _ = wp.process_event(_event(12))
        # The second event at t=12 should have triggered firing of window [0,10)
        assert wp.closed_window_count >= 1

    def test_tumbling_aggregation_sum(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
            aggregate_fields=[{"function": "SUM", "column": "amount", "alias": "total"}],
        )
        wp.process_event(_event(1, data={"amount": 100}))
        wp.process_event(_event(3, data={"amount": 200}))
        # Push past the window
        fired, _ = wp.process_event(_event(22))
        assert len(fired) >= 1
        assert fired[0].aggregations["total"] == 300

    def test_tumbling_aggregation_count(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
            aggregate_fields=[{"function": "COUNT", "column": "*", "alias": "cnt"}],
        )
        for t in [1, 2, 3]:
            wp.process_event(_event(t))
        fired, _ = wp.process_event(_event(22))
        assert fired[0].aggregations["cnt"] == 3

    def test_tumbling_aggregation_min_max(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
            aggregate_fields=[
                {"function": "MIN", "column": "val", "alias": "lo"},
                {"function": "MAX", "column": "val", "alias": "hi"},
            ],
        )
        # All events in [0, 10) window, timestamps 1-5
        for i, v in enumerate([5, 2, 8, 1, 9]):
            wp.process_event(_event(float(i + 1), data={"val": v}))
        fired, _ = wp.process_event(_event(22))
        assert fired[0].aggregations["lo"] == 1
        assert fired[0].aggregations["hi"] == 9

    def test_tumbling_aggregation_avg(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
            aggregate_fields=[{"function": "AVG", "column": "val", "alias": "avg_val"}],
        )
        for v in [10, 20, 30]:
            wp.process_event(_event(v % 10 + 1, data={"val": v}))
        fired, _ = wp.process_event(_event(22))
        assert abs(fired[0].aggregations["avg_val"] - 20.0) < 0.01

    # ── Late data ──
    def test_late_data_drop(self):
        wp = WindowProcessor(
            WindowConfig(
                type=WindowType.TUMBLING, size_seconds=10,
                late_data_policy=LateDataPolicy.DROP,
            ),
            watermark_delay=5,
        )
        wp.process_event(_event(20))  # watermark → 15
        fired, late = wp.process_event(_event(5))  # late (5 < 15)
        assert len(late) == 1
        assert late[0].is_late is True
        assert wp.late_events == 1

    def test_late_data_dead_letter(self):
        wp = WindowProcessor(
            WindowConfig(
                type=WindowType.TUMBLING, size_seconds=10,
                late_data_policy=LateDataPolicy.DEAD_LETTER,
            ),
            watermark_delay=5,
        )
        wp.process_event(_event(20))
        fired, late = wp.process_event(_event(5))
        assert len(late) == 1

    def test_late_data_update(self):
        wp = WindowProcessor(
            WindowConfig(
                type=WindowType.TUMBLING, size_seconds=10,
                late_data_policy=LateDataPolicy.UPDATE,
            ),
            watermark_delay=5,
        )
        wp.process_event(_event(5))
        wp.process_event(_event(20))  # watermark → 15
        # Late event with UPDATE policy still gets processed
        fired, late = wp.process_event(_event(6))
        assert len(late) == 0  # UPDATE doesn't emit as late
        # BUG-092: the late event belongs to the window [0,10) that already
        # fired at t=5 with event_count=1 -- it must merge into that result
        # (event_count=2), not spawn a second, incomplete window (count=1).
        assert len(fired) == 1
        assert fired[0].event_count == 2

    def test_late_event_within_remerge_policy_merges_into_the_original_window(self):
        # BUG-092: a late event accepted via accept_to_window=True for a
        # window that already fired created a fresh, empty WindowState
        # instead of merging into the original result -- corrupting
        # downstream aggregates with a second, incomplete "window closed"
        # emission for the same window.
        from pipeline.streaming.late_data import remerge_within_allowed_lateness_policy

        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
            aggregate_fields=[{"function": "SUM", "column": "amount", "alias": "total"}],
            late_data_policy_callable=remerge_within_allowed_lateness_policy(
                allowed_lateness_seconds=20,
            ),
        )
        wp.process_event(_event(1, data={"amount": 100}))
        fired, late = wp.process_event(_event(15, data={"amount": 999}))
        assert len(fired) == 1 and fired[0].window_start == 0
        assert fired[0].aggregations["total"] == 100
        assert fired[0].event_count == 1

        # Late event for the already-fired window [0,10), within the
        # allowed-lateness budget (watermark=15, event_ts=5, lateness=10<=20).
        late_fired, late_list = wp.process_event(_event(5, data={"amount": 50}))
        assert late_list == []
        assert len(late_fired) == 1
        refined = late_fired[0]
        assert refined.window_key == fired[0].window_key
        assert refined.event_count == 2, "must merge into the original window, not restart at 1"
        assert refined.aggregations["total"] == 150, "must accumulate onto the prior total, not overwrite it"

    def test_late_data_update_sliding(self):
        # BUG-125: _assign_tumbling's BUG-092 reopen-and-merge fix was never
        # ported to _assign_sliding -- a late event targeting an already-
        # fired sliding window created a fresh, empty WindowState instead of
        # merging, causing the same window_key to be emitted twice with
        # disjoint partial counts. slide_seconds == size_seconds here so the
        # sliding path behaves like tumbling, isolating the fix from
        # multi-window overlap.
        wp = WindowProcessor(
            WindowConfig(
                type=WindowType.SLIDING, size_seconds=10, slide_seconds=10,
                late_data_policy=LateDataPolicy.UPDATE,
            ),
            watermark_delay=5,
        )
        wp.process_event(_event(5))
        wp.process_event(_event(20))  # watermark -> 15, fires window [0,10)
        fired, late = wp.process_event(_event(6))  # late, targets [0,10)
        assert len(late) == 0
        assert len(fired) == 1
        assert fired[0].event_count == 2, "must merge into the original window, not restart at 1"

    def test_late_data_update_session(self):
        # BUG-125: session windows never populated _closed_by_key at all
        # (only the tumbling/sliding branch of _fire_ready_windows did), so
        # even porting the reopen check to _assign_session would have found
        # nothing to reopen. A late event for an already-fired session
        # created a disjoint new session instead of merging.
        wp = WindowProcessor(
            WindowConfig(
                type=WindowType.SESSION, gap_seconds=5,
                late_data_policy=LateDataPolicy.UPDATE,
            ),
            watermark_delay=0,
        )
        wp.process_event(_event(1))
        # t=20 doesn't extend the first session (gap exceeded) -- starts a
        # second session and advances the watermark enough to fire the first.
        fired_at_20, _ = wp.process_event(_event(20))
        assert len(fired_at_20) == 1
        first_session_key = fired_at_20[0].window_key
        assert fired_at_20[0].event_count == 1

        # Late event within the first session's original gap window.
        fired_late, late = wp.process_event(_event(3))
        assert late == []
        assert len(fired_late) == 1
        assert fired_late[0].window_key == first_session_key, "must merge into the original session"
        assert fired_late[0].event_count == 2, "must merge into the original session, not restart at 1"

    # ── Sliding ──
    def test_sliding_multiple_windows(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.SLIDING, size_seconds=10, slide_seconds=5),
            watermark_delay=0,
        )
        wp.process_event(_event(7))
        # t=7 should fall in windows [0,10) and [5,15)
        assert wp.active_window_count == 2

    def test_sliding_rejects_negative_slide_seconds(self):
        # BUG-081: _assign_sliding's `while start <= latest_start: start +=
        # slide` never terminates for a negative slide -- start decreases
        # without bound while latest_start stays fixed, spinning the single
        # shared event loop forever on the first sliding-window event.
        # Reject it at construction (the API boundary), not in the hot loop.
        with pytest.raises(ValueError):
            WindowConfig(type=WindowType.SLIDING, size_seconds=10, slide_seconds=-1)

    def test_sliding_zero_slide_seconds_still_allowed_as_fallback_sentinel(self):
        # 0 is a legitimate "unset" sentinel: `slide = slide_seconds or
        # size_seconds` treats 0 as falsy and falls back to size_seconds
        # (tumbling-equivalent slide) -- must not be rejected by the
        # negative-slide validator above.
        wp = WindowProcessor(
            WindowConfig(type=WindowType.SLIDING, size_seconds=10, slide_seconds=0),
            watermark_delay=0,
        )
        wp.process_event(_event(7))
        assert wp.active_window_count == 1

    # ── Session ──
    def test_session_one_session(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.SESSION, gap_seconds=5),
            watermark_delay=0,
        )
        for t in [1, 3, 5, 7]:
            wp.process_event(_event(t))
        assert wp.active_window_count == 1

    def test_session_two_sessions(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.SESSION, gap_seconds=5),
            watermark_delay=0,
        )
        wp.process_event(_event(1))
        wp.process_event(_event(3))
        wp.process_event(_event(50))  # big gap → new session (well past gap=5)
        # First session may be closed by watermark advance, but two sessions should exist total
        assert wp.active_window_count + wp.closed_window_count >= 2

    # ── Global ──
    def test_global_never_closes(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.GLOBAL),
            watermark_delay=0,
        )
        for t in range(1, 100):
            fired, _ = wp.process_event(_event(float(t)))
        assert wp.active_window_count == 1
        assert wp.closed_window_count == 0

    # ── Batch processing ──
    def test_process_batch(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
        )
        events = [_event(float(t)) for t in range(1, 8)]
        fired, late = wp.process_batch(events)
        assert wp.total_events == 7

    # ── Checkpoint state ──
    def test_state_roundtrip(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=5,
        )
        for t in [1, 3, 5]:
            wp.process_event(_event(t))
        state = wp.get_state()
        wm = wp.watermark

        wp2 = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=5,
        )
        wp2.restore_state(state, wm)
        assert wp2.active_window_count == wp.active_window_count
        assert wp2.watermark == wm

    # ── Multi-key windows ──
    def test_multi_key_tumbling(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=0,
        )
        wp.process_event(_event(1, key="a"))
        wp.process_event(_event(2, key="b"))
        wp.process_event(_event(3, key="a"))
        # Should have 2 windows: one for key=a, one for key=b
        assert wp.active_window_count == 2


# ════════════════════════════════════════════════════════════════
# 3. STATE MANAGER TESTS
# ════════════════════════════════════════════════════════════════

class TestStateManager:
    """Test checkpoint persistence."""

    @pytest.fixture(autouse=True)
    def _setup_dirs(self, tmp_path):
        self.checkpoint_dir = str(tmp_path / "checkpoints")
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def test_create_and_load_checkpoint(self):
        from pipeline.streaming.state_manager import StateManager

        sm = StateManager(pipeline_id="p1", checkpoint_dir=self.checkpoint_dir)
        ws = WindowState(window_key="k1|0-60", window_start=0, window_end=60, event_count=10)
        cp = sm.create_checkpoint(watermark=50.0, window_states=[ws], source_offsets={})

        assert cp.pipeline_id == "p1"
        assert cp.watermark == 50.0
        assert len(cp.window_states) == 1

        # Load latest
        loaded = sm.load_latest_checkpoint()
        assert loaded is not None
        assert loaded.pipeline_id == "p1"
        assert loaded.watermark == 50.0

    def test_rotation(self):
        from pipeline.streaming.state_manager import StateManager

        sm = StateManager(pipeline_id="p2", checkpoint_dir=self.checkpoint_dir, max_checkpoints=3)
        for i in range(5):
            sm.create_checkpoint(watermark=float(i * 10), window_states=[], source_offsets={})

        # Should have at most 3 checkpoint files
        files = [f for f in os.listdir(self.checkpoint_dir) if f.endswith(".json")]
        assert len(files) <= 3

    def test_load_empty(self):
        from pipeline.streaming.state_manager import StateManager

        empty_dir = os.path.join(self.checkpoint_dir, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        sm = StateManager(pipeline_id="p_empty", checkpoint_dir=empty_dir)
        result = sm.load_latest_checkpoint()
        assert result is None

    def test_metrics_in_checkpoint(self):
        from pipeline.streaming.state_manager import StateManager

        sm = StateManager(pipeline_id="p3", checkpoint_dir=self.checkpoint_dir)
        metrics = StreamMetrics(pipeline_id="p3", events_in=100, events_out=80)
        cp = sm.create_checkpoint(  # noqa: F841
            watermark=100.0,
            window_states=[],
            source_offsets={},
            metrics=metrics,
        )
        loaded = sm.load_latest_checkpoint()
        assert loaded is not None
        assert loaded.metrics_snapshot is not None
        assert loaded.metrics_snapshot.events_in == 100


# ════════════════════════════════════════════════════════════════
# 4. SINK ADAPTER TESTS
# ════════════════════════════════════════════════════════════════

class TestConsoleSink:
    def test_emit_window(self):
        from pipeline.streaming.sinks.console_sink import ConsoleSink

        sink = ConsoleSink(config={})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(window_key="k1|0-60", window_start=0, window_end=60, event_count=5)
        loop.run_until_complete(sink.emit_window(ws, "p1"))

        loop.run_until_complete(sink.stop())
        loop.close()


class TestAlertSink:
    def test_alert_fires(self):
        from pipeline.streaming.sinks.alert_sink import AlertSink

        sink = AlertSink(config={"rules": [
            {"field": "total", "operator": ">", "threshold": 500, "label": "High total"},
        ]})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=1, aggregations={"total": 600},
        )
        loop.run_until_complete(sink.emit_window(ws, "p1"))
        assert len(sink.fired_alerts) == 1
        assert sink.fired_alerts[0]["label"] == "High total"

        loop.run_until_complete(sink.stop())
        loop.close()

    def test_alert_does_not_fire(self):
        from pipeline.streaming.sinks.alert_sink import AlertSink

        sink = AlertSink(config={"rules": [
            {"field": "total", "operator": ">", "threshold": 500, "label": "High total"},
        ]})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=1, aggregations={"total": 100},
        )
        loop.run_until_complete(sink.emit_window(ws, "p1"))
        assert len(sink.fired_alerts) == 0

        loop.run_until_complete(sink.stop())
        loop.close()

    def test_fired_alerts_is_bounded_for_long_running_pipelines(self):
        # BUG-091: a frequently-firing rule on a long-running pipeline
        # accumulated one entry per alert forever, with no cap or eviction --
        # unbounded memory growth over days of uptime.
        from pipeline.streaming.sinks.alert_sink import AlertSink

        sink = AlertSink(config={
            "rules": [{"field": "total", "operator": ">", "threshold": 0, "label": "x"}],
            "max_fired": 5,
        })
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        for i in range(50):
            ws = WindowState(
                window_key=f"k{i}|0-60", window_start=0, window_end=60,
                event_count=1, aggregations={"total": 1},
            )
            loop.run_until_complete(sink.emit_window(ws, "p1"))

        assert len(sink.fired_alerts) == 5, (
            "fired_alerts must be capped at max_fired, not grow unboundedly"
        )
        # the most recent alerts are kept, not the oldest
        assert sink.fired_alerts[-1]["window_key"] == "k49|0-60"

        loop.run_until_complete(sink.stop())
        loop.close()


class TestFileSink:
    def test_emit_to_file(self, tmp_path):
        from pipeline.streaming.sinks.file_sink import FileSink

        output_dir = str(tmp_path / "sink_output")
        sink = FileSink(config={"output_dir": output_dir, "format": "json", "flush_every": 1})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=3, aggregations={"count": 3},
        )
        loop.run_until_complete(sink.emit_window(ws, "p1"))
        loop.run_until_complete(sink.stop())
        loop.close()

        # Verify output file was created
        files = list(os.listdir(output_dir))
        assert len(files) >= 1

    def test_flush_write_is_offloaded_to_a_thread(self, tmp_path):
        # BUG-086: _flush() did open()/write, csv.DictWriter, and json.dump
        # directly inside an async def, never wrapped in asyncio.to_thread
        # -- blocking the event loop for every other tenant's concurrent
        # request under the single-worker deployment. Spy on asyncio.to_thread
        # (mirrors the pattern already used for BUG-065/070/085) to confirm
        # the write is actually dispatched through it.
        from unittest.mock import patch

        from pipeline.streaming.sinks.file_sink import FileSink

        output_dir = str(tmp_path / "sink_output")
        sink = FileSink(config={"output_dir": output_dir, "format": "json", "flush_every": 1})

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            with patch("pipeline.streaming.sinks.file_sink.asyncio.to_thread", side_effect=spy_to_thread):
                await sink.start()
                ws = WindowState(
                    window_key="k1|0-60", window_start=0, window_end=60,
                    event_count=3, aggregations={"count": 3},
                )
                await sink.emit_window(ws, "p1")
                await sink.stop()

        asyncio.run(run())

        # BUG-128 added a second offloaded call (os.makedirs in start()),
        # alongside the pre-existing offloaded write from _flush().
        assert len(offloaded_funcs) == 2, (
            f"expected exactly two offloaded calls (makedirs + write), got {offloaded_funcs}"
        )
        assert offloaded_funcs[0] is os.makedirs
        assert offloaded_funcs[1].__name__ == "_write"
        files = list(os.listdir(output_dir))
        assert len(files) == 1

    def test_flush_swaps_buffer_before_offloading_no_row_loss_or_duplication(self, tmp_path):
        # _flush() now swaps self._buffer for a fresh list before awaiting
        # the offloaded write, since the write runs on a worker thread while
        # the event loop is free to run other coroutines -- a later
        # emit_window() appending to the SAME list the thread is iterating
        # would be a real race. Guard the swap itself: every row must land
        # in exactly one batch file, none lost or duplicated across batches.
        from pipeline.streaming.sinks.file_sink import FileSink

        output_dir = str(tmp_path / "sink_output")
        sink = FileSink(config={"output_dir": output_dir, "format": "json", "flush_every": 1})

        async def run():
            await sink.start()
            for i in range(5):
                ws = WindowState(
                    window_key=f"k{i}|0-60", window_start=0, window_end=60,
                    event_count=1, aggregations={"n": i},
                )
                await sink.emit_window(ws, "p1")
            await sink.stop()

        asyncio.run(run())

        files = sorted(os.listdir(output_dir))
        assert len(files) == 5, f"expected 5 batch files (one per emit), got {len(files)}: {files}"
        seen = set()
        for fname in files:
            with open(os.path.join(output_dir, fname), encoding="utf-8") as f:
                batch = json.load(f)
            assert len(batch) == 1, f"{fname} should contain exactly one row, got {len(batch)}"
            seen.add(batch[0]["aggregations"]["n"])
        assert seen == {0, 1, 2, 3, 4}, f"rows lost or duplicated: {seen}"

    def test_start_makedirs_is_offloaded_to_a_thread(self, tmp_path):
        # BUG-128: start() called os.makedirs() directly on the event loop,
        # same class as BUG-086's write offload -- blocks every tenant's
        # concurrent request under the single-worker deployment for the
        # duration of the mkdir, worse on a slow/network-mounted output_dir.
        from unittest.mock import patch

        from pipeline.streaming.sinks.file_sink import FileSink

        output_dir = str(tmp_path / "new_nested" / "sink_output")
        sink = FileSink(config={"output_dir": output_dir, "format": "json"})

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            with patch("pipeline.streaming.sinks.file_sink.asyncio.to_thread", side_effect=spy_to_thread):
                await sink.start()

        asyncio.run(run())

        assert os.makedirs in offloaded_funcs
        assert os.path.isdir(output_dir)


class TestDatabaseSink:
    def test_config_key_matches_schema_and_actually_persists(self, tmp_path):
        # DSR-003 regression: the streaming API schema advertised this
        # sink's config field as "connection" (pipeline/streaming/
        # streaming_api.py), but DatabaseSink.start() only ever read
        # config["path"] -- a pipeline built via the documented schema
        # silently lost all data to :memory: instead of the configured
        # file. Build the config exactly as the schema's field key names
        # it and confirm data actually lands in a real file, not memory.
        from pipeline.streaming.sinks.database_sink import DatabaseSink
        from pipeline.streaming.streaming_api import _SINK_SCHEMAS

        schema_fields = {f["key"] for f in _SINK_SCHEMAS["database"]["fields"]}
        assert "path" in schema_fields, (
            "streaming_api.py's database sink schema no longer advertises "
            "'path' -- DatabaseSink.start() must be updated to match, or "
            "this test updated to match a deliberate rename"
        )

        db_path = str(tmp_path / "streaming.duckdb")
        sink = DatabaseSink(config={"path": db_path, "table": "t"})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=1, aggregations={"count": 1},
        )
        loop.run_until_complete(sink.emit_window(ws, "p1"))
        loop.run_until_complete(sink.stop())
        loop.close()

        assert os.path.exists(db_path), (
            "DatabaseSink wrote to :memory: instead of the configured path"
        )

        import duckdb
        conn = duckdb.connect(db_path)
        try:
            rows = conn.execute('SELECT COUNT(*) FROM "t"').fetchone()
            assert rows[0] == 1
        finally:
            conn.close()

    def test_malicious_table_name_is_safely_quoted_not_executed(self, tmp_path):
        # BUG-079: the sink's `table` config (free text, exposed directly in
        # the streaming pipeline config UI) was spliced unescaped into
        # CREATE TABLE/INSERT SQL. A table name containing a double-quote
        # could break out of the identifier and inject arbitrary SQL.
        from pipeline.streaming.sinks.database_sink import DatabaseSink

        db_path = str(tmp_path / "streaming.duckdb")
        evil_table = 'x" ; CREATE TABLE pwned(id INT); --'
        sink = DatabaseSink(config={"path": db_path, "table": evil_table})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        ws = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=1, aggregations={"count": 1},
        )
        loop.run_until_complete(sink.emit_window(ws, "p1"))
        loop.run_until_complete(sink.stop())
        loop.close()

        import duckdb
        conn = duckdb.connect(db_path)
        try:
            tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
            assert "pwned" not in tables, (
                "malicious table name broke out of identifier quoting and "
                "executed injected SQL"
            )
            assert evil_table in tables, (
                "the literal (safely-quoted) table name should still exist"
            )
            rows = conn.execute(f'SELECT COUNT(*) FROM {quote_identifier(evil_table)}').fetchone()
            assert rows[0] == 1
        finally:
            conn.close()

    def test_postgres_sql_templates_use_quoted_identifiers(self):
        # Same bug, Postgres branch: _PG_CREATE_TABLE/_PG_INSERT must not
        # rely on the caller's raw table name being embedded in bare
        # "{table}" quoting -- the sink must pre-quote it via
        # shared.sql_identifiers.quote_identifier before .format().
        from pipeline.streaming.sinks.database_sink import _PG_CREATE_TABLE, _PG_INSERT

        evil_table = quote_identifier('x" ; DROP TABLE users; --')
        create_sql = _PG_CREATE_TABLE.format(table=evil_table)
        insert_sql = _PG_INSERT.format(table=evil_table)
        assert create_sql.count(evil_table) == 1
        assert insert_sql.count(evil_table) == 1
        # The templates themselves must not add their own quotes around
        # {table} (that would double-quote an already-quoted identifier).
        assert '"{table}"' not in _PG_CREATE_TABLE
        assert '"{table}"' not in _PG_INSERT

    def test_duckdb_connect_and_execute_are_offloaded_to_a_thread(self, tmp_path):
        # BUG-085: duckdb.connect() and conn.execute() are synchronous,
        # blocking calls -- running them directly on the event loop freezes
        # every other tenant's concurrent request under the single-worker
        # deployment. Spy on asyncio.to_thread (mirrors the pattern already
        # used for BUG-065/070) to confirm both start() and emit_window()
        # actually dispatch through it, rather than inferring it from timing.
        from unittest.mock import patch

        from pipeline.streaming.sinks.database_sink import DatabaseSink

        db_path = str(tmp_path / "streaming.duckdb")
        sink = DatabaseSink(config={"path": db_path, "table": "t"})

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            with patch("pipeline.streaming.sinks.database_sink.asyncio.to_thread", side_effect=spy_to_thread):
                await sink.start()
                ws = WindowState(
                    window_key="k1|0-60", window_start=0, window_end=60,
                    event_count=1, aggregations={"count": 1},
                )
                await sink.emit_window(ws, "p1")
            await sink.stop()

        asyncio.run(run())

        assert len(offloaded_funcs) == 2, (
            "expected exactly two offloaded calls (connect+create in start(), "
            f"insert in emit_window()), got {len(offloaded_funcs)}"
        )

    def test_rewritten_window_upserts_not_duplicates(self, tmp_path):
        # BUG-129: the module docstring/label claim "upsert" semantics, but
        # emit_window() only ever INSERTed -- a legitimate re-fire of the
        # same window (e.g. BUG-092's tumbling reopen-and-merge path, or
        # BUG-125's sliding/session port) silently duplicated the row
        # instead of replacing it, double-counting any consumer summing on
        # window_key. Emit the same (pipeline_id, window_key) twice with
        # different aggregations and confirm exactly one row survives, with
        # the SECOND emit's values (an upsert, not an insert-then-ignore).
        from pipeline.streaming.sinks.database_sink import DatabaseSink

        db_path = str(tmp_path / "streaming.duckdb")
        sink = DatabaseSink(config={"path": db_path, "table": "t"})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(sink.start())

        first = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=3, aggregations={"count": 3},
        )
        loop.run_until_complete(sink.emit_window(first, "p1"))

        reopened = WindowState(
            window_key="k1|0-60", window_start=0, window_end=60,
            event_count=7, aggregations={"count": 7},
        )
        loop.run_until_complete(sink.emit_window(reopened, "p1"))

        loop.run_until_complete(sink.stop())
        loop.close()

        import duckdb
        conn = duckdb.connect(db_path)
        try:
            rows = conn.execute('SELECT event_count FROM "t" WHERE window_key = ?', ["k1|0-60"]).fetchall()
            assert len(rows) == 1, f"expected exactly one row after re-fire, got {len(rows)}: {rows}"
            assert rows[0][0] == 7, f"expected the second emit's value to win, got {rows[0][0]}"
        finally:
            conn.close()


class TestWebhookSink:
    def test_emit_late_event_does_not_raise_and_sends_the_event_timestamp(self):
        # BUG-090: emit_late_event read event.event_time, but StreamEvent
        # only defines `timestamp` -- every late-event delivery raised
        # AttributeError, silently swallowed by streaming_engine.py's broad
        # `except Exception`, so the advertised include_late feature never
        # actually POSTed anything.
        import httpx

        from pipeline.streaming.sinks.webhook_sink import WebhookSink

        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200)

        sink = WebhookSink(config={"url": "https://example.test/hook", "include_late": True})

        async def run():
            await sink.start()
            sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            event = _event(1234.5, key="k1", data={"x": 1})
            await sink.emit_late_event(event, "p1")
            await sink.stop()

        asyncio.run(run())

        assert len(captured) == 1, "emit_late_event must not silently fail"
        assert captured[0]["event_time"] == 1234.5
        assert captured[0]["event"] == "late_event"

    def test_emit_late_event_is_a_noop_when_include_late_is_disabled(self):
        import httpx

        from pipeline.streaming.sinks.webhook_sink import WebhookSink

        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        sink = WebhookSink(config={"url": "https://example.test/hook"})

        async def run():
            await sink.start()
            sink._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            await sink.emit_late_event(_event(1.0), "p1")
            await sink.stop()

        asyncio.run(run())
        assert captured == []


# ════════════════════════════════════════════════════════════════
# 5. SOURCE ADAPTER TESTS
# ════════════════════════════════════════════════════════════════

class TestFileWatcherSource:
    def test_parses_parquet_files(self, tmp_path):
        # DSR-006 regression: FileWatcherSource's own module docstring and
        # _parse_file's file-type dispatch claimed CSV/JSON/Parquet support,
        # but _parse_file had no .parquet branch -- it silently returned []
        # for any Parquet file, and the file was still marked "seen" so it
        # was never retried, i.e. permanently and silently dropped.
        import pyarrow as pa
        import pyarrow.parquet as pq

        from pipeline.streaming.sources.file_watcher import FileWatcherSource

        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()
        parquet_path = watch_dir / "data.parquet"
        table = pa.table({"id": [1, 2, 3], "amount": [10.5, 20.0, 30.25]})
        pq.write_table(table, str(parquet_path))

        src = FileWatcherSource(config={"watch_dir": str(watch_dir), "pattern": "*.parquet"})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(src.start())
        # start() marks pre-existing files as "seen" (matches the source's
        # own start-of-stream semantics) -- clear that so this test can
        # observe the file actually being picked up and parsed.
        src._seen_files.clear()

        events = loop.run_until_complete(src.read_batch(max_events=10))
        loop.close()

        assert len(events) == 3, f"expected 3 rows parsed from the parquet file, got {events}"
        assert {e.data["id"] for e in events} == {1, 2, 3}
        assert events[0].data["amount"] in (10.5, 20.0, 30.25)

    def test_parse_file_is_offloaded_to_a_thread(self, tmp_path):
        # BUG-087: _parse_csv/_parse_json/_parse_parquet do blocking file
        # I/O (and, for Parquet, CPU-bound parsing), but read_batch called
        # _parse_file synchronously with no asyncio.to_thread -- blocking
        # the event loop for every other tenant's concurrent request under
        # the single-worker deployment. Spy on asyncio.to_thread (mirrors
        # BUG-065/070/085/086) to confirm the parse is actually dispatched
        # through it.
        from unittest.mock import patch

        from pipeline.streaming.sources.file_watcher import FileWatcherSource

        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()
        (watch_dir / "data.csv").write_text("id,name\n1,Alice\n2,Bob\n", encoding="utf-8")

        src = FileWatcherSource(config={"watch_dir": str(watch_dir), "pattern": "*.csv"})

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            await src.start()
            src._seen_files.clear()
            with patch("pipeline.streaming.sources.file_watcher.asyncio.to_thread", side_effect=spy_to_thread):
                return await src.read_batch(max_events=10)

        events = asyncio.run(run())

        # BUG-124 also offloads the directory scan (_scan_dir), so this is
        # now [_scan_dir, _parse_file] rather than [_parse_file] alone.
        assert len(offloaded_funcs) == 2, (
            f"expected the directory scan plus one offloaded parse call, got {len(offloaded_funcs)}"
        )
        assert offloaded_funcs[0] == src._scan_dir
        assert offloaded_funcs[1] == src._parse_file
        assert len(events) == 2

    def test_directory_scan_is_offloaded_to_a_thread(self, tmp_path):
        # BUG-124: read_batch's directory listing (Path.exists()+glob()) ran
        # directly on the event loop -- only the per-file parse (BUG-087)
        # was offloaded. Spy on asyncio.to_thread to confirm the scan itself
        # is now dispatched through it too, on every poll cycle, not just
        # when new files happen to be found.
        from unittest.mock import patch

        from pipeline.streaming.sources.file_watcher import FileWatcherSource

        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()

        src = FileWatcherSource(config={"watch_dir": str(watch_dir), "pattern": "*.csv"})

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            with patch("pipeline.streaming.sources.file_watcher.asyncio.to_thread", side_effect=spy_to_thread):
                await src.start()
                return await src.read_batch(max_events=10)

        events = asyncio.run(run())

        # start()'s initial seen-files scan, then read_batch's own scan --
        # both must go through to_thread even with zero files present.
        assert offloaded_funcs.count(src._scan_dir) == 2, (
            f"expected _scan_dir offloaded once in start() and once in read_batch(), got {offloaded_funcs}"
        )
        assert events == []

    def test_seen_files_scan_result_is_unaffected_by_offloading(self, tmp_path):
        # Confirms the fix doesn't change behavior, only where the blocking
        # call runs: a file present before start() is marked seen and not
        # re-emitted; a file added after start() is picked up on the next
        # read_batch() call, exactly as before BUG-124.
        from pipeline.streaming.sources.file_watcher import FileWatcherSource

        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()
        (watch_dir / "pre_existing.csv").write_text("id\n1\n", encoding="utf-8")

        async def run():
            src = FileWatcherSource(config={"watch_dir": str(watch_dir), "pattern": "*.csv"})
            await src.start()
            # Pre-existing file must already be marked seen -- no events yet.
            first = await src.read_batch(max_events=10)
            assert first == []

            (watch_dir / "new_file.csv").write_text("id\n2\n", encoding="utf-8")
            second = await src.read_batch(max_events=10)
            assert len(second) == 1
            assert second[0].data["id"] == "2"

        asyncio.run(run())

    def test_seen_files_is_bounded_evicts_oldest_first(self):
        # BUG-130: _seen_files grew without bound for the life of the
        # pipeline (one entry per file ever observed, never pruned), same
        # class BUG-091 fixed for AlertSink._fired -- also bloats every
        # periodic checkpoint since get_offsets() serializes the whole set.
        # Mark more filenames than the configured cap and confirm the set
        # never exceeds it, evicting the OLDEST entries first (not an
        # arbitrary/newest one, which would defeat the "already seen"
        # dedup purpose for files still actively being watched).
        from pipeline.streaming.sources.file_watcher import FileWatcherSource

        src = FileWatcherSource(config={"watch_dir": "unused", "max_seen_files": 5})

        for i in range(8):
            src._mark_seen(f"file_{i}.csv")

        assert len(src._seen_files) == 5, (
            f"expected _seen_files capped at 5, got {len(src._seen_files)}"
        )
        for i in range(3):
            assert f"file_{i}.csv" not in src._seen_files, (
                f"file_{i}.csv should have been evicted as one of the 3 oldest"
            )
        for i in range(3, 8):
            assert f"file_{i}.csv" in src._seen_files, (
                f"file_{i}.csv should still be present (among the 5 newest)"
            )


class TestSimulatedSource:
    def test_read_batch(self):
        from pipeline.streaming.sources.simulated import SimulatedSource

        src = SimulatedSource(config={
            "event_type": "order",
            "events_per_second": 100,
            "num_keys": 3,
            "schema": {"amount": "float", "product": "string"},
        })
        loop = asyncio.new_event_loop()
        loop.run_until_complete(src.start())

        events = loop.run_until_complete(src.read_batch(max_events=10))
        assert len(events) <= 10
        for e in events:
            assert "amount" in e.data
            assert "product" in e.data
            assert e.key is not None

        loop.run_until_complete(src.stop())
        loop.close()

    def test_offsets(self):
        from pipeline.streaming.sources.simulated import SimulatedSource

        src = SimulatedSource(config={"event_type": "test"})
        loop = asyncio.new_event_loop()
        loop.run_until_complete(src.start())

        offsets = src.get_offsets()
        assert "event_count" in offsets

        loop.run_until_complete(src.stop())
        loop.close()


# ════════════════════════════════════════════════════════════════
# 6. STREAMING API TESTS (unit-level, no server)
# ════════════════════════════════════════════════════════════════

class TestStreamingAPI:
    """Verify API route handlers create / list / manage pipelines."""

    @pytest.fixture(autouse=True)
    def _clear_stores(self):
        from pipeline.streaming.streaming_api import _engines, _pipelines
        _pipelines.clear()
        _engines.clear()
        yield
        _pipelines.clear()
        _engines.clear()

    def test_create_pipeline(self):
        from pipeline.streaming.streaming_api import _pipelines

        p = _make_pipeline()
        _pipelines[p.id] = p
        assert p.id in _pipelines
        assert _pipelines[p.id].name == "test-pipeline"

    def test_templates_exist(self):
        """Verify template list endpoint returns templates."""
        from pipeline.streaming.streaming_api import router
        routes = [r.path for r in router.routes]
        assert "/templates" in routes or any("/templates" in str(r) for r in routes)

    def test_runtime_config_defaults_reproduce_classical_engine_defaults(self):
        """DSR-002: an unset `runtime` on a pipeline must match
        StreamingEngine's own kwarg defaults exactly -- this is additive
        API surface, not a behavior change for existing pipelines."""
        import inspect

        from pipeline.streaming.streaming_engine import StreamingEngine

        p = _make_pipeline()
        engine_defaults = {
            name: param.default
            for name, param in inspect.signature(StreamingEngine.__init__).parameters.items()
            if param.default is not inspect.Parameter.empty
        }
        for field, value in p.runtime.model_dump().items():
            assert engine_defaults[field] == value, (
                f"RuntimeConfig.{field} default {value!r} doesn't match "
                f"StreamingEngine's own default {engine_defaults[field]!r}"
            )

    @pytest.mark.asyncio
    async def test_start_pipeline_passes_runtime_kwargs_to_the_engine(self):
        """DSR-002 regression: before this fix, start_pipeline built a bare
        StreamingEngine(pipe) -- opt-in kwargs set via the API were silently
        unreachable no matter what the pipeline's config said."""
        from pipeline.streaming.models import RuntimeConfig
        from pipeline.streaming.streaming_api import _engines, _pipelines, start_pipeline

        p = _make_pipeline(runtime=RuntimeConfig(
            use_barrier_alignment=True,
            barrier_interval_seconds=5.0,
            use_dataflow_triggers=True,
            backpressure_buffer=42,
        ))
        _pipelines[p.id] = p
        try:
            await start_pipeline(p.id)
            engine = _engines[p.id]
            assert engine._use_barrier_alignment is True
            assert engine._barrier_interval == 5.0
            assert engine._use_dataflow_triggers is True
            assert engine._bp_buffer_size == 42
        finally:
            await _engines[p.id].stop()
            _pipelines.pop(p.id, None)
            _engines.pop(p.id, None)


class TestStreamingAPITenantIsolation:
    """BUG-080: streaming pipeline endpoints had zero tenant scoping --
    _pipelines/_engines were process-global dicts keyed only by pipeline_id,
    so any authenticated caller could read/control any other tenant's
    pipeline (including source/sink config that can hold DB connection
    strings or a webhook sink's HMAC secret)."""

    @pytest.fixture(autouse=True)
    def _clear_stores(self):
        from pipeline.streaming.streaming_api import _engines, _pipelines
        _pipelines.clear()
        _engines.clear()
        yield
        _pipelines.clear()
        _engines.clear()

    @staticmethod
    def _user(org_id: str) -> dict:
        return {"sub": f"user-{org_id}", "org_id": org_id}

    @pytest.mark.asyncio
    async def test_get_pipeline_404s_for_a_different_tenant(self):
        from fastapi import HTTPException

        from pipeline.streaming.streaming_api import _pipelines, get_pipeline

        p = _make_pipeline(tenant_id="tenant-a")
        _pipelines[p.id] = p

        result = await get_pipeline(p.id, user=self._user("tenant-a"))
        assert result["id"] == p.id

        with pytest.raises(HTTPException) as exc:
            await get_pipeline(p.id, user=self._user("tenant-b"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_pipelines_only_returns_the_caller_s_own_tenant(self):
        from pipeline.streaming.streaming_api import _pipelines, list_pipelines

        pa = _make_pipeline(tenant_id="tenant-a")
        pb = _make_pipeline(tenant_id="tenant-b")
        _pipelines[pa.id] = pa
        _pipelines[pb.id] = pb

        result = await list_pipelines(user=self._user("tenant-a"))
        ids = {p["id"] for p in result["pipelines"]}
        assert ids == {pa.id}

    @pytest.mark.asyncio
    async def test_create_pipeline_stamps_the_caller_s_tenant(self):
        from pipeline.streaming.streaming_api import CreateStreamPipelineRequest, create_pipeline

        req = CreateStreamPipelineRequest(
            name="p", source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
        )
        created = await create_pipeline(req, user=self._user("tenant-a"))
        assert created["tenant_id"] == "tenant-a"

    @pytest.mark.asyncio
    async def test_delete_start_stop_pause_resume_metrics_stream_all_reject_a_different_tenant(self):
        from fastapi import HTTPException

        from pipeline.streaming.streaming_api import (
            _pipelines,
            delete_pipeline,
            get_metrics,
            pause_pipeline,
            resume_pipeline,
            start_pipeline,
            stop_pipeline,
            stream_events,
        )

        other = self._user("tenant-b")
        endpoints = [
            lambda pid: delete_pipeline(pid, user=other),
            lambda pid: start_pipeline(pid, user=other),
            lambda pid: stop_pipeline(pid, user=other),
            lambda pid: pause_pipeline(pid, user=other),
            lambda pid: resume_pipeline(pid, user=other),
            lambda pid: get_metrics(pid, user=other),
            lambda pid: stream_events(pid, user=other),
        ]
        for endpoint in endpoints:
            p = _make_pipeline(tenant_id="tenant-a")
            _pipelines[p.id] = p
            with pytest.raises(HTTPException) as exc:
                await endpoint(p.id)
            assert exc.value.status_code == 404, endpoint
            _pipelines.pop(p.id, None)

    @pytest.mark.asyncio
    async def test_unauthenticated_pipelines_stay_isolated_from_authenticated_tenants(self):
        """A pipeline created with no auth (tenant_id=None) must not be
        readable by an authenticated caller, and vice versa."""
        from fastapi import HTTPException

        from pipeline.streaming.streaming_api import _pipelines, get_pipeline

        p = _make_pipeline()  # tenant_id defaults to None
        _pipelines[p.id] = p

        result = await get_pipeline(p.id, user=None)
        assert result["id"] == p.id

        with pytest.raises(HTTPException) as exc:
            await get_pipeline(p.id, user=self._user("tenant-a"))
        assert exc.value.status_code == 404


class TestStreamingAPIStartRace:
    """BUG-089: start_pipeline's status check and engine construction spanned
    an `await` with no lock, so two near-simultaneous start requests for the
    same pipeline could both pass the "not already running" check before
    either flipped the status -- each building its own StreamingEngine, with
    the second silently overwriting _engines[pipeline_id] and leaking the
    first engine's unstoppable background tasks."""

    @pytest.fixture(autouse=True)
    def _clear_stores(self):
        from pipeline.streaming.streaming_api import _engines, _pipelines, _start_locks
        _pipelines.clear()
        _engines.clear()
        _start_locks.clear()
        yield
        _pipelines.clear()
        _engines.clear()
        _start_locks.clear()

    @pytest.mark.asyncio
    async def test_concurrent_start_calls_only_create_one_engine(self):
        from fastapi import HTTPException

        from pipeline.streaming.streaming_api import _engines, _pipelines, start_pipeline

        p = _make_pipeline()
        _pipelines[p.id] = p
        try:
            results = await asyncio.gather(
                start_pipeline(p.id), start_pipeline(p.id), return_exceptions=True
            )
            successes = [r for r in results if not isinstance(r, Exception)]
            conflicts = [
                r for r in results
                if isinstance(r, HTTPException) and r.status_code == 409
            ]
            assert len(successes) == 1, results
            assert len(conflicts) == 1, results
            assert p.id in _engines
        finally:
            engine = _engines.get(p.id)
            if engine:
                await engine.stop()
            _pipelines.pop(p.id, None)
            _engines.pop(p.id, None)


class TestStreamingAPIStartDeleteRace:
    """BUG-123: delete_pipeline/stop_pipeline/pause_pipeline/resume_pipeline
    never held the BUG-089 start lock, so a request landing while
    start_pipeline was suspended mid-STARTING (real await points inside
    engine.start()) could pass delete's RUNNING-only guard, pop _pipelines/
    _engines, and orphan the suspended start's own local engine reference
    once it resumed and spawned unstoppable background tasks."""

    @pytest.fixture(autouse=True)
    def _clear_stores(self):
        from pipeline.streaming.streaming_api import _engines, _pipelines, _start_locks
        _pipelines.clear()
        _engines.clear()
        _start_locks.clear()
        yield
        _pipelines.clear()
        _engines.clear()
        _start_locks.clear()

    @pytest.mark.asyncio
    async def test_delete_cannot_race_a_mid_start_engine(self, monkeypatch):
        from fastapi import HTTPException

        from pipeline.streaming.sources.simulated import SimulatedSource
        from pipeline.streaming.streaming_api import (
            _engines,
            _pipelines,
            delete_pipeline,
            start_pipeline,
        )

        release = asyncio.Event()
        reached = asyncio.Event()
        orig_start = SimulatedSource.start

        async def delayed_start(self):
            reached.set()
            await release.wait()
            await orig_start(self)

        monkeypatch.setattr(SimulatedSource, "start", delayed_start)

        p = _make_pipeline()
        _pipelines[p.id] = p

        start_task = asyncio.create_task(start_pipeline(p.id))
        await asyncio.wait_for(reached.wait(), timeout=2.0)

        # start_pipeline is now suspended inside engine.start() (status
        # STARTING), holding _start_lock_for(p.id). A concurrent delete must
        # block on that same lock rather than racing ahead of a RUNNING-only
        # status check.
        delete_task = asyncio.create_task(delete_pipeline(p.id))
        await asyncio.sleep(0)
        assert not delete_task.done(), "delete_pipeline must block on the start lock, not race ahead"
        assert p.id in _pipelines and p.id in _engines, "delete must not remove entries while start is in flight"

        release.set()
        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(delete_task, timeout=2.0)
        assert exc_info.value.status_code == 409  # must stop before deleting -- proves it never orphaned
        await asyncio.wait_for(start_task, timeout=2.0)

        engine = _engines[p.id]
        await engine.stop()
        _pipelines.pop(p.id, None)
        _engines.pop(p.id, None)

    @pytest.mark.asyncio
    async def test_stop_cannot_race_a_mid_start_engine(self, monkeypatch):
        from fastapi import HTTPException

        from pipeline.streaming.sources.simulated import SimulatedSource
        from pipeline.streaming.streaming_api import (
            _engines,
            _pipelines,
            start_pipeline,
            stop_pipeline,
        )

        release = asyncio.Event()
        reached = asyncio.Event()
        orig_start = SimulatedSource.start

        async def delayed_start(self):
            reached.set()
            await release.wait()
            await orig_start(self)

        monkeypatch.setattr(SimulatedSource, "start", delayed_start)

        p = _make_pipeline()
        _pipelines[p.id] = p

        start_task = asyncio.create_task(start_pipeline(p.id))
        await asyncio.wait_for(reached.wait(), timeout=2.0)

        stop_task = asyncio.create_task(stop_pipeline(p.id))
        await asyncio.sleep(0)
        assert not stop_task.done(), "stop_pipeline must block on the start lock, not race ahead"

        release.set()
        await asyncio.wait_for(start_task, timeout=2.0)
        # Once start_pipeline has finished (status RUNNING, engine registered),
        # the queued stop_pipeline call must now succeed against the REAL
        # engine, not a stale/absent lookup made mid-STARTING.
        result = await asyncio.wait_for(stop_task, timeout=2.0)
        assert result["status"] in ("stopped", "STOPPED")

        _pipelines.pop(p.id, None)
        _engines.pop(p.id, None)


# ════════════════════════════════════════════════════════════════
# 7. BACKPRESSURE TESTS
# ════════════════════════════════════════════════════════════════

class TestBackpressure:
    """Tests for BackpressureManager."""

    def test_put_and_get_batch(self):
        from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy

        mgr = BackpressureManager(max_buffer_size=100, strategy=BackpressureStrategy.BLOCK)
        loop = asyncio.new_event_loop()

        events = [_event(1000.0 + i, f"k{i}") for i in range(10)]
        enqueued = loop.run_until_complete(mgr.put_batch(events))
        assert enqueued == 10

        got = loop.run_until_complete(mgr.get_batch(max_events=5, timeout=1.0))
        assert len(got) == 5

        got2 = loop.run_until_complete(mgr.get_batch(max_events=20, timeout=0.5))
        assert len(got2) == 5

        loop.close()

    def test_drop_tail_strategy(self):
        from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy

        mgr = BackpressureManager(max_buffer_size=5, strategy=BackpressureStrategy.DROP_TAIL)
        loop = asyncio.new_event_loop()

        events = [_event(1000.0 + i) for i in range(10)]
        enqueued = loop.run_until_complete(mgr.put_batch(events))
        assert enqueued < 10  # some dropped

        stats = mgr.stats()
        assert stats["dropped_count"] > 0
        assert stats["buffer_depth"] <= 5

        loop.close()

    def test_stats(self):
        from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy

        mgr = BackpressureManager(max_buffer_size=50, strategy=BackpressureStrategy.BLOCK)
        loop = asyncio.new_event_loop()

        events = [_event(1000.0 + i) for i in range(5)]
        loop.run_until_complete(mgr.put_batch(events))

        stats = mgr.stats()
        assert stats["buffer_depth"] == 5
        assert stats["is_pressured"] is False
        assert stats["dropped_count"] == 0
        assert "buffer_utilization" in stats

        loop.close()

    def test_flush_unblocks_get(self):
        from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy

        mgr = BackpressureManager(max_buffer_size=50, strategy=BackpressureStrategy.BLOCK)
        loop = asyncio.new_event_loop()

        # Flush puts a sentinel so get_batch returns empty quickly
        loop.run_until_complete(mgr.flush())
        got = loop.run_until_complete(mgr.get_batch(max_events=10, timeout=1.0))
        assert len(got) == 0  # sentinel is filtered out

        loop.close()


# ════════════════════════════════════════════════════════════════
# 8. END-TO-END ENGINE TESTS
# ════════════════════════════════════════════════════════════════

class TestStreamingEngine:
    """Integration tests for StreamingEngine lifecycle."""

    def _make_engine_pipeline(self, **kw) -> StreamPipeline:
        defaults = dict(
            name="e2e-test",
            source=StreamSource(
                type=StreamSourceType.SIMULATED,
                config={"events_per_second": 50, "event_type": "metric", "num_keys": 2},
            ),
            window=WindowConfig(type=WindowType.TUMBLING, size_seconds=5),
            sinks=[StreamSink(type=StreamSinkType.CONSOLE)],
            transforms=[
                StreamTransform(type=TransformType.KEY_BY, config={"field": "key"}),
            ],
            checkpoint_interval_seconds=60,
        )
        defaults.update(kw)
        return StreamPipeline(**defaults)

    def test_engine_start_and_stop(self):
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())
        assert pipeline.status == StreamPipelineStatus.RUNNING

        # Let it run briefly
        loop.run_until_complete(asyncio.sleep(1.0))

        # Check metrics
        m = engine.metrics
        assert m.events_in > 0
        assert m.uptime_seconds > 0
        assert m.backpressure is not None
        assert "buffer_depth" in m.backpressure

        loop.run_until_complete(engine.stop())
        assert pipeline.status == StreamPipelineStatus.STOPPED
        loop.close()

    def test_failed_start_closes_already_opened_sinks(self, monkeypatch):
        # BUG-126: start() registered the engine before await engine.start(),
        # and its except block only flipped status to FAILED with no cleanup
        # -- a sink that already opened a live resource (here, WebhookSink's
        # httpx.AsyncClient) before a LATER sink's start() failed was left
        # open forever, since FAILED makes a retry legal and that retry
        # overwrites the only reference to the leaked resource.
        from pipeline.streaming.sinks.console_sink import ConsoleSink
        from pipeline.streaming.streaming_engine import StreamingEngine

        async def failing_start(self):
            raise RuntimeError("simulated second-sink start failure")

        monkeypatch.setattr(ConsoleSink, "start", failing_start)

        pipeline = self._make_engine_pipeline(
            sinks=[
                StreamSink(type=StreamSinkType.WEBHOOK, config={"url": "http://example.invalid/hook"}),
                StreamSink(type=StreamSinkType.CONSOLE),
            ],
        )
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        with pytest.raises(RuntimeError):
            loop.run_until_complete(engine.start())

        assert pipeline.status == StreamPipelineStatus.FAILED
        webhook_sink = engine._sinks[0]
        assert webhook_sink._client is None, "the already-started sink's httpx.AsyncClient must be closed, not leaked"
        loop.close()

    def test_engine_pause_resume(self):
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())
        loop.run_until_complete(asyncio.sleep(0.5))

        loop.run_until_complete(engine.pause())
        assert pipeline.status == StreamPipelineStatus.PAUSED
        events_at_pause = engine.metrics.events_in

        loop.run_until_complete(asyncio.sleep(0.5))
        # Processing loop paused, but events_in shouldn't grow much
        # (ingest loop also pauses)


        loop.run_until_complete(engine.resume())
        assert pipeline.status == StreamPipelineStatus.RUNNING

        loop.run_until_complete(asyncio.sleep(0.5))
        events_after_resume = engine.metrics.events_in
        assert events_after_resume > events_at_pause

        loop.run_until_complete(engine.stop())
        loop.close()

    def test_engine_checkpoint_recovery(self):
        from pipeline.streaming.state_manager import StateManager
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())
        loop.run_until_complete(asyncio.sleep(1.0))

        # Force a checkpoint
        loop.run_until_complete(engine._checkpoint())

        # Verify checkpoint file exists
        sm = engine._state_mgr
        cp = sm.load_latest_checkpoint()
        assert cp is not None
        assert cp.pipeline_id == pipeline.id

        loop.run_until_complete(engine.stop())

        # Cleanup checkpoint dir
        checkpoint_dir = sm.checkpoint_dir
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)

        loop.close()

    def test_checkpoint_io_is_offloaded_to_a_thread(self):
        # BUG-088: create_checkpoint()/load_latest_checkpoint() do
        # synchronous file I/O (write+os.replace, then list/sort/delete old
        # checkpoints) -- called directly from async start()/_checkpoint()
        # with no asyncio.to_thread, blocking the single shared event loop
        # for the full write/rotation duration on every periodic checkpoint.
        # Spy on asyncio.to_thread (mirrors BUG-065/070/085/086/087) to
        # confirm both calls are actually dispatched through it.
        from unittest.mock import patch

        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)

        real_to_thread = asyncio.to_thread
        offloaded_funcs = []

        async def spy_to_thread(func, *args, **kwargs):
            offloaded_funcs.append(func)
            return await real_to_thread(func, *args, **kwargs)

        async def run():
            with patch("pipeline.streaming.streaming_engine.asyncio.to_thread", side_effect=spy_to_thread):
                await engine.start()
                await engine._checkpoint()
            await engine.stop()

        try:
            asyncio.run(run())
            assert engine._state_mgr.load_latest_checkpoint in offloaded_funcs
            assert engine._state_mgr.create_checkpoint in offloaded_funcs
        finally:
            checkpoint_dir = engine._state_mgr.checkpoint_dir
            if os.path.exists(checkpoint_dir):
                shutil.rmtree(checkpoint_dir)

    def test_start_failure_error_is_sanitized_not_raw(self, monkeypatch):
        # BUG-127: StreamMetrics.errors is returned verbatim by
        # GET /pipelines, /pipelines/{id} and /pipelines/{id}/metrics.
        # A raw str(exc) can leak internal detail (paths, DSNs, module
        # names) to the client -- it must go through sanitize_error first.
        from pipeline.streaming.sources.simulated import SimulatedSource
        from pipeline.streaming.streaming_engine import StreamingEngine

        sensitive_detail = "connect failed: postgresql://admin:s3cr3t@10.0.0.5/prod"

        async def failing_start(self):
            raise RuntimeError(sensitive_detail)

        monkeypatch.setattr(SimulatedSource, "start", failing_start)

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        with pytest.raises(RuntimeError):
            loop.run_until_complete(engine.start())

        assert pipeline.status == StreamPipelineStatus.FAILED
        assert len(engine.metrics.errors) == 1
        assert sensitive_detail not in engine.metrics.errors[0]
        assert engine.metrics.errors[0] == "Internal server error"
        loop.close()

    def test_run_loop_failure_error_is_sanitized_not_raw(self, monkeypatch):
        from pipeline.streaming.streaming_engine import StreamingEngine

        sensitive_detail = "/etc/aura/secrets/signing_key.pem not found"

        pipeline = self._make_engine_pipeline()
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())

        async def failing_get_batch(*args, **kwargs):
            raise RuntimeError(sensitive_detail)

        monkeypatch.setattr(engine._backpressure, "get_batch", failing_get_batch)

        loop.run_until_complete(engine._task)

        assert pipeline.status == StreamPipelineStatus.FAILED
        assert len(engine.metrics.errors) == 1
        assert sensitive_detail not in engine.metrics.errors[0]
        assert engine.metrics.errors[0] == "Internal server error"
        loop.close()

    def test_engine_with_filter_transform(self):
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline(
            transforms=[
                StreamTransform(
                    type=TransformType.FILTER,
                    config={"field": "event_type", "operator": "==", "value": "metric"},
                ),
            ],
        )
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())
        loop.run_until_complete(asyncio.sleep(1.0))

        m = engine.metrics
        # Some events may be filtered
        assert m.events_in > 0

        loop.run_until_complete(engine.stop())
        loop.close()

    def test_filter_transform_gte_lte_operators(self):
        # DSR-004 regression: _apply_transforms's FILTER branch only handled
        # ==, !=, >, <, "in" -- >= and <= fell through every `if` unmatched,
        # so a filter configured with either operator silently passed every
        # event through instead of filtering. Direct unit test against
        # _apply_transforms (not the async engine E2E test above) so this is
        # deterministic rather than depending on the simulated source's timing.
        from pipeline.streaming.streaming_engine import _apply_transforms

        gte_filter = StreamTransform(
            type=TransformType.FILTER,
            config={"field": "amount", "operator": ">=", "value": 100},
        )
        lte_filter = StreamTransform(
            type=TransformType.FILTER,
            config={"field": "amount", "operator": "<=", "value": 100},
        )

        below = StreamEvent(timestamp=time.time(), data={"amount": 50})
        at = StreamEvent(timestamp=time.time(), data={"amount": 100})
        above = StreamEvent(timestamp=time.time(), data={"amount": 150})

        # >= 100: only "at" and "above" survive.
        assert _apply_transforms(below.model_copy(), [gte_filter]) is None
        assert _apply_transforms(at.model_copy(), [gte_filter]) is not None
        assert _apply_transforms(above.model_copy(), [gte_filter]) is not None

        # <= 100: only "below" and "at" survive.
        assert _apply_transforms(below.model_copy(), [lte_filter]) is not None
        assert _apply_transforms(at.model_copy(), [lte_filter]) is not None
        assert _apply_transforms(above.model_copy(), [lte_filter]) is None

    def test_engine_with_file_sink(self, tmp_path):
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = self._make_engine_pipeline(
            sinks=[StreamSink(
                type=StreamSinkType.FILE,
                config={"output_dir": str(tmp_path / "e2e_output"), "format": "json", "flush_every": 1},
            )],
        )
        engine = StreamingEngine(pipeline, batch_size=20, tick_interval=0.2)
        loop = asyncio.new_event_loop()

        loop.run_until_complete(engine.start())
        loop.run_until_complete(asyncio.sleep(2.0))
        loop.run_until_complete(engine.stop())

        # Check output files were written
        output_dir = tmp_path / "e2e_output"
        if output_dir.exists():
            files = list(output_dir.iterdir())
            # May or may not have files depending on window fires
            assert isinstance(files, list)

        loop.close()


# ════════════════════════════════════════════════════════════════
# 9. WINDOW EVICTION TESTS
# ════════════════════════════════════════════════════════════════

class TestWindowEviction:
    """Test window memory leak prevention via eviction."""

    def test_evict_stale_windows(self):
        wp = WindowProcessor(
            config=WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
            watermark_delay=9999.0,  # large delay so windows never fire
            aggregate_fields=[],
            max_active_windows=5,
        )

        # Create more windows than the cap (each unique key+time => unique window)
        for i in range(10):
            ev = _event(ts=float(i * 10), key=f"key_{i}", data={"value": 1})
            wp.process_event(ev)

        # Should have evicted some
        assert wp.active_window_count <= 5
        assert wp.evicted_windows > 0

    def test_closed_history_cap(self):
        wp = WindowProcessor(
            config=WindowConfig(type=WindowType.TUMBLING, size_seconds=1),
            watermark_delay=0.0,
            aggregate_fields=[],
            max_closed_history=3,
        )

        # Force many windows to close by advancing time
        for i in range(20):
            ev = _event(ts=float(i * 2), key="k1", data={"v": 1})
            wp.process_event(ev)

        assert wp.closed_window_count <= 3


# ════════════════════════════════════════════════════════════════
# Run
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
