"""
Streaming Engine
=================
The main runtime loop for a streaming pipeline:

  Source → [Transform] → Window Processor → Sinks
                ↕
          State Manager (checkpoint / recovery)

Features:
  - Async micro-batch loop (configurable batch size & interval)
  - Pluggable source / sink adapters
  - Event-time windowing with watermark tracking
  - Automatic checkpointing and recovery from latest checkpoint
  - Live metrics broadcast via SSE sink
  - Graceful start / pause / resume / stop lifecycle
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from pipeline.streaming.models import (
    LateDataPolicy,
    StreamEvent,
    StreamMetrics,
    StreamPipeline,
    StreamPipelineStatus,
    TransformType,
    WindowConfig,
)
from pipeline.streaming.sources.base import BaseSource
from pipeline.streaming.sources.simulated import SimulatedSource
from pipeline.streaming.sources.file_watcher import FileWatcherSource
from pipeline.streaming.sources.kafka_source import KafkaSource
from pipeline.streaming.sinks.base import BaseSink
from pipeline.streaming.sinks.sse_sink import SSESink
from pipeline.streaming.sinks.console_sink import ConsoleSink
from pipeline.streaming.sinks.database_sink import DatabaseSink
from pipeline.streaming.sinks.file_sink import FileSink
from pipeline.streaming.sinks.alert_sink import AlertSink
from pipeline.streaming.sinks.kafka_sink import KafkaSink
from pipeline.streaming.window_processor import WindowProcessor
from pipeline.streaming.state_manager import StateManager
from pipeline.streaming.backpressure import BackpressureManager, BackpressureStrategy

logger = logging.getLogger("aura.streaming.engine")


# ────────────────────────────────────────────────────────────────────
# Factory helpers
# ────────────────────────────────────────────────────────────────────

def _create_source(pipeline: StreamPipeline) -> BaseSource:
    src = pipeline.source
    cfg = src.config
    if src.type.value == "simulated":
        return SimulatedSource(cfg)
    if src.type.value == "file_watcher":
        return FileWatcherSource(cfg)
    if src.type.value == "kafka":
        return KafkaSource(cfg)
    raise ValueError(f"Unsupported source type: {src.type}")


def _create_sink(sink_def) -> BaseSink:
    t = sink_def.type.value
    cfg = sink_def.config
    if t == "sse":
        return SSESink(cfg)
    if t == "console":
        return ConsoleSink(cfg)
    if t == "database":
        return DatabaseSink(cfg)
    if t == "file":
        return FileSink(cfg)
    if t == "alert":
        return AlertSink(cfg)
    if t == "kafka":
        return KafkaSink(cfg)
    raise ValueError(f"Unsupported sink type: {t}")


# ────────────────────────────────────────────────────────────────────
# Transform applicator
# ────────────────────────────────────────────────────────────────────

def _apply_transforms(event: StreamEvent, transforms) -> Optional[StreamEvent]:
    """Apply a chain of transforms to a single event. Returns None if filtered."""
    for t in transforms:
        if t.type == TransformType.FILTER:
            # config: {"field": "status", "operator": "==", "value": "active"}
            field = t.config.get("field")
            op = t.config.get("operator", "==")
            value = t.config.get("value")
            actual = event.data.get(field)
            if op == "==" and actual != value:
                return None
            if op == "!=" and actual == value:
                return None
            if op == ">" and (actual is None or float(actual) <= float(value)):
                return None
            if op == "<" and (actual is None or float(actual) >= float(value)):
                return None
            if op == "in" and actual not in (value if isinstance(value, list) else [value]):
                return None

        elif t.type == TransformType.MAP:
            # config: {"mappings": {"new_field": "old_field", ...}, "drop": ["field_to_remove"]}
            mappings = t.config.get("mappings", {})
            for new_key, old_key in mappings.items():
                if old_key in event.data:
                    event.data[new_key] = event.data[old_key]
            for drop_field in t.config.get("drop", []):
                event.data.pop(drop_field, None)

        elif t.type == TransformType.KEY_BY:
            # config: {"field": "region"}
            field = t.config.get("field")
            if field and field in event.data:
                event.key = str(event.data[field])

    return event


# ────────────────────────────────────────────────────────────────────
# Streaming Engine
# ────────────────────────────────────────────────────────────────────

class StreamingEngine:
    """
    Runs a single streaming pipeline.

    Usage:
        engine = StreamingEngine(pipeline)
        await engine.start()     # begins micro-batch loop
        await engine.stop()      # graceful shutdown
        metrics = engine.metrics # real-time stats
    """

    def __init__(
        self,
        pipeline: StreamPipeline,
        batch_size: int = 100,
        tick_interval: float = 0.5,
        backpressure_buffer: int = 10_000,
        backpressure_strategy: str = "block",
    ):
        self.pipeline = pipeline
        self.batch_size = batch_size
        self.tick_interval = tick_interval

        # Components
        self._source: Optional[BaseSource] = None
        self._sinks: List[BaseSink] = []
        self._window_proc: Optional[WindowProcessor] = None
        self._state_mgr: Optional[StateManager] = None
        self._backpressure: Optional[BackpressureManager] = None

        # Backpressure config
        self._bp_buffer_size = backpressure_buffer
        self._bp_strategy = BackpressureStrategy(backpressure_strategy)

        # Runtime
        self._task: Optional[asyncio.Task] = None
        self._ingest_task: Optional[asyncio.Task] = None
        self._paused = False
        self._start_time: float = 0.0

        # Metrics
        self._metrics = StreamMetrics(pipeline_id=pipeline.id)

    # ── Properties ────────────────────────────────────────────────

    @property
    def metrics(self) -> StreamMetrics:
        m = self._metrics
        m.status = StreamPipelineStatus(self.pipeline.status)
        if self._window_proc:
            m.watermark_position = self._window_proc.watermark
            m.active_windows = self._window_proc.active_window_count
            m.closed_windows = self._window_proc.closed_window_count
        if self._start_time > 0 and self.pipeline.status == StreamPipelineStatus.RUNNING:
            m.uptime_seconds = round(time.time() - self._start_time, 1)
        if self._backpressure:
            bp_stats = self._backpressure.stats()
            m.backpressure = bp_stats
        if self._state_mgr and self._state_mgr.last_checkpoint_time > 0:
            m.last_checkpoint_at = datetime.fromtimestamp(
                self._state_mgr.last_checkpoint_time, tz=timezone.utc
            ).isoformat()
        return m

    def get_sse_sink(self) -> Optional[SSESink]:
        """Return the SSE sink instance if one is configured."""
        for s in self._sinks:
            if isinstance(s, SSESink):
                return s
        return None

    # ── Lifecycle ─────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the streaming pipeline."""
        if self.pipeline.status == StreamPipelineStatus.RUNNING:
            logger.warning("Pipeline %s already running", self.pipeline.id)
            return

        self.pipeline.status = StreamPipelineStatus.STARTING
        logger.info("Starting streaming pipeline: %s (%s)", self.pipeline.name, self.pipeline.id)

        try:
            # Create source adapter
            self._source = _create_source(self.pipeline)
            await self._source.start()

            # Create sink adapters
            self._sinks = [_create_sink(s) for s in self.pipeline.sinks]
            for sink in self._sinks:
                await sink.start()

            # Create window processor
            agg_fields = []
            for t in self.pipeline.transforms:
                if t.type == TransformType.AGGREGATE:
                    agg_fields = t.config.get("fields", [])
                    break
            self._window_proc = WindowProcessor(
                config=self.pipeline.window,
                watermark_delay=float(self.pipeline.watermark_delay_seconds),
                aggregate_fields=agg_fields,
            )

            # State manager + recovery
            self._state_mgr = StateManager(self.pipeline.id)
            checkpoint = self._state_mgr.load_latest_checkpoint()
            if checkpoint:
                logger.info("Recovering from checkpoint: watermark=%.1f, windows=%d",
                            checkpoint.watermark, len(checkpoint.window_states))
                self._window_proc.restore_state(checkpoint.window_states, checkpoint.watermark)
                if self._source:
                    await self._source.commit_offsets(checkpoint.source_offsets)

            # Backpressure buffer
            self._backpressure = BackpressureManager(
                max_buffer_size=self._bp_buffer_size,
                strategy=self._bp_strategy,
            )

            # Start main loop + ingest loop
            self.pipeline.status = StreamPipelineStatus.RUNNING
            self._start_time = time.time()
            self._paused = False
            self._ingest_task = asyncio.create_task(self._ingest_loop())
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Pipeline %s is now RUNNING", self.pipeline.id)

        except Exception as e:
            self.pipeline.status = StreamPipelineStatus.FAILED
            self._metrics.errors.append(str(e))
            logger.error("Failed to start pipeline %s: %s", self.pipeline.id, e)
            raise

    async def stop(self) -> None:
        """Graceful shutdown: flush, checkpoint, close all."""
        if self.pipeline.status not in (
            StreamPipelineStatus.RUNNING,
            StreamPipelineStatus.PAUSED,
        ):
            return

        self.pipeline.status = StreamPipelineStatus.STOPPING
        logger.info("Stopping pipeline %s ...", self.pipeline.id)

        # Cancel ingest loop
        if self._ingest_task and not self._ingest_task.done():
            self._ingest_task.cancel()
            try:
                await self._ingest_task
            except asyncio.CancelledError:
                pass

        # Cancel processing loop
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Flush backpressure buffer
        if self._backpressure:
            await self._backpressure.flush()

        # Final checkpoint
        await self._checkpoint()

        # Close sinks
        for sink in self._sinks:
            try:
                await sink.stop()
            except Exception as e:
                logger.error("Error stopping sink: %s", e)

        # Close source
        if self._source:
            try:
                await self._source.stop()
            except Exception as e:
                logger.error("Error stopping source: %s", e)

        self.pipeline.status = StreamPipelineStatus.STOPPED
        logger.info("Pipeline %s stopped", self.pipeline.id)

    async def pause(self) -> None:
        self._paused = True
        self.pipeline.status = StreamPipelineStatus.PAUSED
        logger.info("Pipeline %s paused", self.pipeline.id)

    async def resume(self) -> None:
        self._paused = False
        self.pipeline.status = StreamPipelineStatus.RUNNING
        logger.info("Pipeline %s resumed", self.pipeline.id)

    # ── Ingest Loop ────────────────────────────────────────────────

    async def _ingest_loop(self) -> None:
        """Read from source and push into backpressure buffer."""
        logger.info("Ingest loop started for %s", self.pipeline.id)
        try:
            while True:
                if self._paused:
                    await asyncio.sleep(self.tick_interval)
                    continue
                events = await self._source.read_batch(self.batch_size)
                if events:
                    await self._backpressure.put_batch(events)
                else:
                    await asyncio.sleep(self.tick_interval)
        except asyncio.CancelledError:
            logger.info("Ingest loop cancelled for %s", self.pipeline.id)
        except Exception as e:
            logger.error("Ingest loop error for %s: %s", self.pipeline.id, e, exc_info=True)

    # ── Main Loop ─────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Micro-batch loop: read → transform → window → sink."""
        logger.info("Engine loop started for %s", self.pipeline.id)
        tick_count = 0
        try:
            while True:
                if self._paused:
                    await asyncio.sleep(self.tick_interval)
                    continue

                # 1. Read micro-batch from backpressure buffer
                events = await self._backpressure.get_batch(self.batch_size)

                if events:
                    # 2. Apply transforms
                    transformed: List[StreamEvent] = []
                    for ev in events:
                        result = _apply_transforms(ev, self.pipeline.transforms)
                        if result is not None:
                            transformed.append(result)

                    self._metrics.events_in += len(events)
                    self._metrics.events_dropped += len(events) - len(transformed)

                    # 3. Window processing
                    all_fired = []
                    all_late = []
                    for ev in transformed:
                        fired, late = self._window_proc.process_event(ev)
                        all_fired.extend(fired)
                        all_late.extend(late)

                    self._metrics.events_late += len(all_late)

                    # 4. Emit fired windows to sinks
                    for window in all_fired:
                        self._metrics.events_out += window.event_count
                        for sink in self._sinks:
                            try:
                                await sink.emit_window(window, self.pipeline.id)
                            except Exception as e:
                                logger.error("Sink emit error: %s", e)

                    # 5. Emit late events to sinks (dead letter)
                    if self.pipeline.window.late_data_policy == LateDataPolicy.DEAD_LETTER:
                        for late_ev in all_late:
                            for sink in self._sinks:
                                try:
                                    await sink.emit_late_event(late_ev, self.pipeline.id)
                                except Exception:
                                    pass

                # 6. EPS calculation
                elapsed = time.time() - self._start_time
                if elapsed > 0:
                    self._metrics.events_per_second = round(
                        self._metrics.events_in / elapsed, 1
                    )

                # 7. Periodic checkpoint
                if self._state_mgr and self._state_mgr.should_checkpoint(
                    self.pipeline.checkpoint_interval_seconds
                ):
                    await self._checkpoint()

                # 8. Broadcast metrics via SSE
                tick_count += 1
                if tick_count % 4 == 0:  # every 4 ticks (~2 sec)
                    await self._broadcast_metrics()

                await asyncio.sleep(self.tick_interval)

        except asyncio.CancelledError:
            logger.info("Engine loop cancelled for %s", self.pipeline.id)
        except Exception as e:
            self.pipeline.status = StreamPipelineStatus.FAILED
            self._metrics.errors.append(str(e))
            logger.error("Engine loop error for %s: %s", self.pipeline.id, e, exc_info=True)

    # ── Internal helpers ──────────────────────────────────────────

    async def _checkpoint(self) -> None:
        if not self._state_mgr or not self._window_proc:
            return
        try:
            window_states = self._window_proc.get_state()
            offsets = self._source.get_offsets() if self._source else {}
            self._state_mgr.create_checkpoint(
                watermark=self._window_proc.watermark,
                window_states=window_states,
                source_offsets=offsets,
                metrics=self._metrics,
            )
        except Exception as e:
            logger.error("Checkpoint failed: %s", e)

    async def _broadcast_metrics(self) -> None:
        sse = self.get_sse_sink()
        if sse:
            try:
                m = self.metrics
                await sse.emit_metrics(m.model_dump())
            except Exception:
                pass
