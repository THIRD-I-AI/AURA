"""
Streaming Pipeline Data Models
================================
Typed definitions for real-time streaming pipelines:
  - Sources (Kafka, CDC, file watcher, simulated)
  - Temporal windows (tumbling, sliding, session)
  - Event-time semantics with watermarks
  - Late data handling policies
  - Stateful transforms with checkpoint support
  - Sinks (SSE, database, file, alert)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ────────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────────

class StreamSourceType(str, Enum):
    """Where streaming data originates."""
    KAFKA = "kafka"
    FILE_WATCHER = "file_watcher"       # watches a directory for new files
    CDC = "cdc"                          # change data capture (PostgreSQL)
    WEBSOCKET = "websocket"
    SIMULATED = "simulated"              # generates fake events for testing/demo


class StreamSinkType(str, Enum):
    """Where processed streaming data goes."""
    SSE = "sse"                          # Server-Sent Events → frontend
    DATABASE = "database"                # write to PostgreSQL/DuckDB
    FILE = "file"                        # micro-batch write (Parquet/CSV)
    KAFKA = "kafka"                      # emit to another Kafka topic
    ALERT = "alert"                      # trigger when condition is met
    CONSOLE = "console"                  # log to stdout (debugging)
    WEBHOOK = "webhook"                  # POST closed windows to an HTTP endpoint


class WindowType(str, Enum):
    """Temporal window strategies for stream aggregation."""
    TUMBLING = "tumbling"      # fixed-size, non-overlapping
    SLIDING = "sliding"        # fixed-size, overlapping (slides by interval)
    SESSION = "session"        # groups by activity gap
    GLOBAL = "global"          # single window across all time


class LateDataPolicy(str, Enum):
    """How to handle events that arrive after the watermark."""
    DROP = "drop"              # silently discard late events
    UPDATE = "update"          # re-open window and update aggregation
    DEAD_LETTER = "dead_letter"  # route to a dead-letter sink


class StreamPipelineStatus(str, Enum):
    """Lifecycle states of a streaming pipeline."""
    DRAFT = "draft"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TransformType(str, Enum):
    """Processing operations available in streaming transforms."""
    FILTER = "filter"
    MAP = "map"                # project / rename / add columns
    AGGREGATE = "aggregate"    # SUM, COUNT, AVG, MIN, MAX within window
    FLAT_MAP = "flat_map"      # one event → zero or more events
    KEY_BY = "key_by"          # set the grouping key for windows


# ────────────────────────────────────────────────────────────────────
# Core Event Model
# ────────────────────────────────────────────────────────────────────

class StreamEvent(BaseModel):
    """A single event flowing through the streaming pipeline."""
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float                  # event time (Unix epoch seconds)
    key: Optional[str] = None         # partition / grouping key
    data: Dict[str, Any] = Field(default_factory=dict)
    source: Optional[str] = None      # originating source label
    is_late: bool = False             # flagged if arrived after watermark


# ────────────────────────────────────────────────────────────────────
# Window Configuration
# ────────────────────────────────────────────────────────────────────

class WindowConfig(BaseModel):
    """Configuration for temporal windowing."""
    type: WindowType = WindowType.TUMBLING
    size_seconds: int = 60             # window duration
    slide_seconds: Optional[int] = None  # for SLIDING: how far the window moves
    gap_seconds: Optional[int] = None    # for SESSION: inactivity gap
    late_data_policy: LateDataPolicy = LateDataPolicy.DROP
    allowed_lateness_seconds: int = 10   # grace period for late arrivals

    @field_validator("slide_seconds")
    @classmethod
    def _slide_seconds_must_not_be_negative(cls, v: Optional[int]) -> Optional[int]:
        # BUG-081: window_processor.py's _assign_sliding walks
        # `start -= slide` in an unbounded loop when slide is negative --
        # start decreases forever while latest_start stays fixed, spinning
        # the single shared event loop forever on the first sliding-window
        # event. Reject it here, at the API boundary, rather than in the
        # hot processing path. 0 stays legal: _assign_sliding treats it as
        # falsy and falls back to size_seconds (tumbling-equivalent slide).
        if v is not None and v < 0:
            raise ValueError("slide_seconds must not be negative")
        return v


# ────────────────────────────────────────────────────────────────────
# Runtime Configuration (DSR-002)
# ────────────────────────────────────────────────────────────────────
# StreamingEngine (streaming_engine.py) has long accepted opt-in kwargs for
# triggers, watermarks, barrier-alignment, and backpressure (Sprint S20.1) --
# but streaming_api.py's start_pipeline constructs it as bare
# StreamingEngine(pipe), so none of this was reachable from the live HTTP
# API. This is the API surface: field names and defaults mirror
# StreamingEngine.__init__'s kwargs exactly, so a pipeline's runtime dict
# can be passed straight through as **kwargs. `late_data_policy_callable`
# is deliberately excluded -- it's a Python callable, not something a JSON
# API can carry; WindowConfig.late_data_policy already covers the
# API-representable policy choice.

class RuntimeConfig(BaseModel):
    """Opt-in execution primitives for a streaming pipeline's engine.

    Every field defaults to StreamingEngine's own default, so an unset
    ``runtime`` reproduces the exact classical (pre-S20.1) behavior --
    this is additive API surface, not a behavior change for existing
    pipelines.
    """
    backpressure_buffer: int = 10_000
    backpressure_strategy: Literal["block", "drop_tail", "sample"] = "block"

    use_pid_backpressure: bool = False
    pid_target_utilization: float = 0.7
    pid_kp: float = 0.5
    pid_ki: float = 0.1
    pid_kd: float = 0.05
    pid_max_sleep_seconds: float = 1.0

    use_composite_watermark_tracker: bool = False
    upstream_ids: Optional[List[str]] = None

    use_dataflow_triggers: bool = False

    use_barrier_alignment: bool = False
    barrier_interval_seconds: float = 30.0


# ────────────────────────────────────────────────────────────────────
# Transform Step
# ────────────────────────────────────────────────────────────────────

class StreamTransform(BaseModel):
    """One processing operation in the streaming pipeline."""
    id: str = Field(default_factory=lambda: f"st_{uuid.uuid4().hex[:6]}")
    type: TransformType
    description: str = ""
    config: Dict[str, Any] = Field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────
# Source / Sink Definitions
# ────────────────────────────────────────────────────────────────────

class StreamSource(BaseModel):
    """Defines where streaming data originates."""
    type: StreamSourceType
    config: Dict[str, Any] = Field(default_factory=dict)
    # Common configs per type:
    # KAFKA:        {topic, bootstrap_servers, group_id, auto_offset_reset}
    # FILE_WATCHER: {watch_dir, pattern, poll_interval_seconds}
    # CDC:          {host, port, database, table, username, password, slot_name}
    # WEBSOCKET:    {url, headers}
    # SIMULATED:    {event_type, events_per_second, num_keys, schema}

    def label(self) -> str:
        if self.type == StreamSourceType.KAFKA:
            return f"kafka://{self.config.get('topic', '?')}"
        if self.type == StreamSourceType.FILE_WATCHER:
            return f"watch://{self.config.get('watch_dir', '?')}"
        if self.type == StreamSourceType.CDC:
            return f"cdc://{self.config.get('table', '?')}"
        if self.type == StreamSourceType.SIMULATED:
            return f"sim://{self.config.get('event_type', 'default')}"
        return f"{self.type.value}://?"


class StreamSink(BaseModel):
    """Defines where processed data is emitted."""
    type: StreamSinkType
    config: Dict[str, Any] = Field(default_factory=dict)
    # Common configs per type:
    # SSE:      {channel}
    # DATABASE: {connection, table, if_exists}
    # FILE:     {output_dir, format, flush_interval_seconds}
    # KAFKA:    {topic, bootstrap_servers}
    # ALERT:    {condition, channel, message_template}
    # CONSOLE:  {}


# ────────────────────────────────────────────────────────────────────
# Pipeline Definition
# ────────────────────────────────────────────────────────────────────

class StreamPipeline(BaseModel):
    """Complete streaming pipeline definition."""
    id: str = Field(default_factory=lambda: f"spipe_{uuid.uuid4().hex[:8]}")
    name: str
    description: str = ""

    # Source
    source: StreamSource

    # Event time
    event_time_field: str = "timestamp"
    watermark_delay_seconds: int = 10

    # Windowing
    window: WindowConfig = Field(default_factory=WindowConfig)

    # Execution primitives (DSR-002): triggers/watermarks/barrier-alignment/
    # backpressure, passed straight through to StreamingEngine's constructor.
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    # Processing
    transforms: List[StreamTransform] = Field(default_factory=list)

    # Sinks (fan-out: one pipeline → multiple sinks)
    sinks: List[StreamSink] = Field(default_factory=list)

    # Checkpoint
    checkpoint_interval_seconds: int = 30

    # Metadata
    status: StreamPipelineStatus = StreamPipelineStatus.DRAFT
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    # Tenant isolation (BUG-080): the caller's tenant at creation time, from
    # the verified JWT (see streaming_api.py's `_request_tenant`). `None`
    # means the pipeline was created unauthenticated (dev/open mode) and is
    # only visible to other unauthenticated callers.
    tenant_id: Optional[str] = None


# ────────────────────────────────────────────────────────────────────
# Runtime State Models
# ────────────────────────────────────────────────────────────────────

class WindowState(BaseModel):
    """State of a single window during processing."""
    window_key: str                    # e.g. "region=US"
    window_start: float                # epoch seconds
    window_end: float                  # epoch seconds
    event_count: int = 0
    aggregations: Dict[str, float] = Field(default_factory=dict)
    last_event_time: float = 0.0
    is_closed: bool = False


class StreamMetrics(BaseModel):
    """Real-time metrics for a running streaming pipeline."""
    pipeline_id: str
    status: StreamPipelineStatus = StreamPipelineStatus.STOPPED
    events_in: int = 0
    events_out: int = 0
    events_late: int = 0
    events_dropped: int = 0
    events_per_second: float = 0.0
    watermark_position: float = 0.0     # current watermark (epoch seconds)
    active_windows: int = 0
    closed_windows: int = 0
    last_checkpoint_at: Optional[str] = None
    uptime_seconds: float = 0.0
    backpressure: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)


class CheckpointData(BaseModel):
    """Serialisable checkpoint for recovery."""
    pipeline_id: str
    checkpoint_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    watermark: float = 0.0
    window_states: List[WindowState] = Field(default_factory=list)
    source_offsets: Dict[str, Any] = Field(default_factory=dict)
    metrics_snapshot: Optional[StreamMetrics] = None
