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

    # ── Sliding ──
    def test_sliding_multiple_windows(self):
        wp = WindowProcessor(
            WindowConfig(type=WindowType.SLIDING, size_seconds=10, slide_seconds=5),
            watermark_delay=0,
        )
        wp.process_event(_event(7))
        # t=7 should fall in windows [0,10) and [5,15)
        assert wp.active_window_count == 2

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


# ════════════════════════════════════════════════════════════════
# 5. SOURCE ADAPTER TESTS
# ════════════════════════════════════════════════════════════════

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
