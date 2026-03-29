"""
AURA Streaming Pipeline Engine
================================
Real-time data pipeline processing with:
  - Pluggable source adapters (Kafka, file watcher, CDC, simulated)
  - Temporal windowing (tumbling, sliding, session)
  - Event-time semantics with watermarks and late-data handling
  - Stateful processing with checkpoint/recovery
  - Pluggable sink adapters (SSE, database, file micro-batch, alert)
"""

from pipeline.streaming.models import (
    StreamSourceType,
    StreamSinkType,
    WindowType,
    LateDataPolicy,
    StreamEvent,
    WindowConfig,
    StreamTransform,
    StreamSource,
    StreamSink,
    StreamPipeline,
    StreamPipelineStatus,
    StreamMetrics,
    WindowState,
    CheckpointData,
)
from pipeline.streaming.streaming_engine import StreamingEngine
from pipeline.streaming.window_processor import WindowProcessor
from pipeline.streaming.state_manager import StateManager

__all__ = [
    "StreamSourceType",
    "StreamSinkType",
    "WindowType",
    "LateDataPolicy",
    "StreamEvent",
    "WindowConfig",
    "StreamTransform",
    "StreamSource",
    "StreamSink",
    "StreamPipeline",
    "StreamPipelineStatus",
    "StreamMetrics",
    "WindowState",
    "CheckpointData",
    "StreamingEngine",
    "WindowProcessor",
    "StateManager",
]
