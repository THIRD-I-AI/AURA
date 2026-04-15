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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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
