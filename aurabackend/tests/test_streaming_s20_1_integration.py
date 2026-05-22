"""
Sprint S20.1 — integration tests for the S20a streaming primitives
wired into the live streaming modules (BackpressureManager,
WindowProcessor, StreamingEngine).

Scope (single bundle per the brainstorm decision):

  * PID backpressure (BackpressureManager + PIDBackpressureController)
  * Composite watermark tracker (WindowProcessor + WatermarkTracker)
  * Dataflow triggers (WindowProcessor + WatermarkTrigger via TriggerContext)
  * Parametric late-data policy callable (WindowProcessor + late_data.py)
  * Barrier-aligned checkpointing (StreamingEngine + BarrierAligner)

The contract under test: when the opt-in flag is OFF (default), the
classical path is byte-identical to pre-S20.1 — verified separately
by the 64 existing tests in tests/test_streaming.py +
tests/test_streaming_pid.py. This file adds the FLAG-ON contracts on
top.

All tests Tier A (pure-Python, no optional deps).
"""
from __future__ import annotations

import asyncio
import time
from typing import List

import pytest

from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy
from pipeline.streaming.late_data import (
    LateDataDecision,
    remerge_within_allowed_lateness_policy,
)
from pipeline.streaming.models import (
    LateDataPolicy,
    StreamEvent,
    WindowConfig,
    WindowType,
)
from pipeline.streaming.window_processor import WindowProcessor

# ── Phase 1: BackpressureManager + PID ────────────────────────────────


class TestPIDBackpressure:
    def test_flag_off_returns_zero_sleep(self) -> None:
        """Classical mode → compute_ingest_sleep_seconds is a no-op
        (returns 0.0) so the ingest loop runs at full speed."""
        bp = BackpressureManager(max_buffer_size=100)
        assert bp.compute_ingest_sleep_seconds(dt=1.0, max_sleep_seconds=2.0) == 0.0

    def test_flag_on_constructs_pid_controller(self) -> None:
        """When use_pid_control=True, the PID controller is lazy-built
        with target = b_max * pid_target_utilization."""
        bp = BackpressureManager(
            max_buffer_size=100,
            use_pid_control=True,
            pid_target_utilization=0.7,
        )
        assert bp._pid is not None

    @pytest.mark.asyncio
    async def test_pid_sleep_grows_with_buffer_depth(self) -> None:
        """**PID convergence contract**: when buffer is empty, u(t) ≈ 0
        and sleep ≈ 0. As buffer fills past target, u(t) climbs toward
        1 and sleep approaches max_sleep_seconds."""
        bp = BackpressureManager(
            max_buffer_size=100,
            use_pid_control=True,
            pid_target_utilization=0.5,  # target = 50
            pid_kp=2.0, pid_ki=0.0, pid_kd=0.0,  # P-only for predictability
        )
        # Empty buffer: error = 0 - target = -50 → u clamped at 0 → sleep=0.
        s0 = bp.compute_ingest_sleep_seconds(dt=1.0, max_sleep_seconds=1.0)
        assert s0 == 0.0
        # Fill buffer past target.
        for _ in range(80):
            bp._buffer.put_nowait(StreamEvent(data={}, timestamp=0.0))
        # Buffer=80, target=50, error=+30 → u positive → sleep > 0.
        s1 = bp.compute_ingest_sleep_seconds(dt=1.0, max_sleep_seconds=1.0)
        assert s1 > 0.0, f"PID should throttle when overfull, got sleep={s1}"

    def test_pid_stats_surface_diagnostics(self) -> None:
        """Operator card needs PID state in stats() — last u, last dt,
        controller metrics."""
        bp = BackpressureManager(
            max_buffer_size=100,
            use_pid_control=True,
        )
        bp.compute_ingest_sleep_seconds(dt=1.0, max_sleep_seconds=1.0)
        stats = bp.stats()
        assert "pid" in stats
        assert stats["pid"]["enabled"] is True
        assert "last_u" in stats["pid"]


# ── Phase 2: WindowProcessor + composite watermark / triggers / late-data


def _ev(ts: float, key: str = "k", source: str = "src1", value: float = 1.0) -> StreamEvent:
    """StreamEvent helper. The model's upstream-label field is `source`
    (not source_id — Pydantic would silently drop unknown fields)."""
    return StreamEvent(data={"v": value}, timestamp=ts, key=key, source=source)


class TestCompositeWatermark:
    def test_flag_off_uses_single_upstream_advance(self) -> None:
        """Default config → no tracker. Watermark advances as
        max(event_time - watermark_delay)."""
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(config=cfg, watermark_delay=5.0)
        assert wp._tracker is None
        wp.process_event(_ev(ts=100.0))
        assert wp.watermark == 95.0

    def test_flag_on_constructs_tracker(self) -> None:
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(
            config=cfg,
            watermark_delay=5.0,
            use_composite_watermark_tracker=True,
            upstream_ids=["src1", "src2"],
        )
        assert wp._tracker is not None

    def test_composite_watermark_is_min_of_inputs(self) -> None:
        """**Composite watermark contract**: min across all upstreams.
        Slowest upstream gates window closure."""
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(
            config=cfg,
            watermark_delay=5.0,
            use_composite_watermark_tracker=True,
            upstream_ids=["src1", "src2"],
        )
        # src1 at t=100 (wm contribution = 95) then src2 at t=50 (wm
        # contribution = 45). Composite = min(95, 45) = 45 — slowest
        # upstream gates progress.
        wp.process_event(_ev(ts=100.0, source="src1"))
        wp.process_event(_ev(ts=50.0, source="src2"))
        assert wp.watermark == 45.0


class TestDataflowTriggers:
    def test_flag_off_uses_inline_watermark_check(self) -> None:
        """Default firing path: watermark >= window_end."""
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(config=cfg, watermark_delay=0.0)
        # Window [0, 10) — fire when watermark crosses 10.
        wp.process_event(_ev(ts=5.0))
        assert wp.active_window_count == 1
        # Push event at t=20: watermark advances to 20 → window [0,10) fires.
        fired, _ = wp.process_event(_ev(ts=20.0))
        # Both windows: [0,10) is now closed; [10,20) opens and may not close
        # immediately. At least one fired.
        assert any(ws.window_end == 10 for ws in fired)

    def test_flag_on_routes_through_trigger(self) -> None:
        """Same semantic outcome (WatermarkTrigger fires when wm >=
        window_end) but the dispatch goes through Trigger.should_fire."""
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(
            config=cfg,
            watermark_delay=0.0,
            use_dataflow_triggers=True,
        )
        wp.process_event(_ev(ts=5.0))
        fired, _ = wp.process_event(_ev(ts=20.0))
        assert any(ws.window_end == 10 for ws in fired), (
            "WatermarkTrigger path should fire window [0,10) once wm >= 10"
        )

    def test_first_processing_ts_tracked_when_triggers_enabled(self) -> None:
        """ProcessingTimeTrigger needs first-event wall-clock per
        window — verify the bookkeeping happens when triggers are on."""
        cfg = WindowConfig(type=WindowType.TUMBLING, size_seconds=10)
        wp = WindowProcessor(
            config=cfg,
            watermark_delay=0.0,
            use_dataflow_triggers=True,
        )
        wp.process_event(_ev(ts=5.0))
        assert len(wp._window_first_processing_ts) == 1


class TestParametricLatePolicy:
    def test_flag_off_uses_enum_dispatch(self) -> None:
        """Without callable, classical LateDataPolicy.DROP/DEAD_LETTER
        dispatch fires."""
        cfg = WindowConfig(
            type=WindowType.TUMBLING, size_seconds=10,
            late_data_policy=LateDataPolicy.DROP,
        )
        wp = WindowProcessor(config=cfg, watermark_delay=0.0)
        wp.process_event(_ev(ts=100.0))  # advances wm to 100
        # Now send a late event (ts=5, watermark=100).
        fired, late = wp.process_event(_ev(ts=5.0))
        assert len(late) == 1
        assert late[0].is_late is True

    def test_flag_on_uses_callable_decision(self) -> None:
        """When callable is set, it overrides enum dispatch. A policy
        that returns accept_to_window=True should NOT mark the event
        as routed-to-late list."""
        cfg = WindowConfig(
            type=WindowType.TUMBLING, size_seconds=10,
            late_data_policy=LateDataPolicy.DROP,  # would normally drop
        )
        # Custom policy: always accept (forces refire).
        def always_accept(event, ts, wm):
            return LateDataDecision(accept_to_window=True, is_within_lateness=True)

        wp = WindowProcessor(
            config=cfg, watermark_delay=0.0,
            late_data_policy_callable=always_accept,
        )
        wp.process_event(_ev(ts=100.0))  # advance wm
        fired, late = wp.process_event(_ev(ts=5.0))  # late event
        # accept_to_window=True → late list stays empty; event flows
        # through to window accumulation on the main path.
        assert late == []

    def test_remerge_policy_accepts_within_lateness(self) -> None:
        """The remerge_within_allowed_lateness factory should accept
        late events within the lateness window."""
        cfg = WindowConfig(
            type=WindowType.TUMBLING, size_seconds=10,
            late_data_policy=LateDataPolicy.DROP,
        )
        wp = WindowProcessor(
            config=cfg, watermark_delay=0.0,
            late_data_policy_callable=remerge_within_allowed_lateness_policy(
                allowed_lateness_seconds=20.0,
                on_expiry="drop",
            ),
        )
        wp.process_event(_ev(ts=100.0))  # wm=100
        # Event at ts=85 → lateness = 100-85 = 15 < 20 → accept.
        fired, late = wp.process_event(_ev(ts=85.0))
        assert late == []
        # Event at ts=50 → lateness = 50 > 20 → expiry policy (drop).
        fired, late = wp.process_event(_ev(ts=50.0))
        assert len(late) == 1


# ── Phase 3: StreamingEngine + BarrierAligner ────────────────────────


class TestBarrierAlignment:
    def test_flag_off_does_not_construct_aligner(self) -> None:
        """Default engine config → no barrier aligner."""
        from pipeline.streaming.models import (
            StreamPipeline,
            StreamSink,
            StreamSinkType,
            StreamSource,
            StreamSourceType,
        )
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = StreamPipeline(
            id="test",
            name="test",
            source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
            sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
            window=WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
        )
        engine = StreamingEngine(pipeline=pipeline)
        assert engine._use_barrier_alignment is False
        assert engine._barrier_aligner is None

    def test_inject_barrier_single_channel_aligns_immediately(self) -> None:
        """Single-source pipeline → one channel → first
        receive_barrier returns ALIGNED. The integration must surface
        that as `aligned=True` so the checkpoint runs."""
        from pipeline.streaming.barrier import BarrierAligner
        from pipeline.streaming.models import (
            StreamPipeline,
            StreamSink,
            StreamSinkType,
            StreamSource,
            StreamSourceType,
        )
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = StreamPipeline(
            id="test",
            name="test",
            source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
            sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
            window=WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
        )
        engine = StreamingEngine(
            pipeline=pipeline,
            use_barrier_alignment=True,
            barrier_interval_seconds=0.0,  # fire immediately
        )
        # Manually construct aligner the way start() would.
        engine._barrier_aligner = BarrierAligner(input_channels=["simulated"])
        engine._last_barrier_emit_ts = 0.0
        # _inject_barrier on a 1-channel aligner returns aligned=True.
        assert engine._inject_barrier() is True

    def test_should_emit_barrier_respects_interval(self) -> None:
        """Barrier interval gates emit cadence — operators don't want
        every tick to inject a barrier."""
        from pipeline.streaming.models import (
            StreamPipeline,
            StreamSink,
            StreamSinkType,
            StreamSource,
            StreamSourceType,
        )
        from pipeline.streaming.streaming_engine import StreamingEngine

        pipeline = StreamPipeline(
            id="test",
            name="test",
            source=StreamSource(type=StreamSourceType.SIMULATED, config={}),
            sinks=[StreamSink(type=StreamSinkType.CONSOLE, config={})],
            window=WindowConfig(type=WindowType.TUMBLING, size_seconds=10),
        )
        engine = StreamingEngine(
            pipeline=pipeline,
            use_barrier_alignment=True,
            barrier_interval_seconds=10.0,
        )
        engine._last_barrier_emit_ts = time.time()
        # Just-emitted → should NOT re-emit immediately.
        assert engine._should_emit_barrier() is False
        # 11s ago → SHOULD emit.
        engine._last_barrier_emit_ts = time.time() - 11.0
        assert engine._should_emit_barrier() is True
