"""
Base Sink Adapter
==================
Abstract interface for all streaming sink adapters.
"""
from __future__ import annotations

import abc
from typing import Any, Dict, List

from pipeline.streaming.models import StreamEvent, WindowState
from shared.exceptions import AuraError


class StreamSinkError(AuraError):
    """A sink could not deliver a window or late event after its own retries.
    BUG-569: a sink must raise this rather than log and return, or the engine
    never learns the write failed and the pipeline keeps reporting RUNNING. The
    message is shown to the client (sanitize_error trusts AuraError messages),
    so it must never carry the underlying exception's text -- chain it instead."""

    error_code = "STREAM_SINK_ERROR"


class BaseSink(abc.ABC):
    """Abstract base for streaming sink adapters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialise the sink connection."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Cleanly shut down the sink."""
        ...

    @abc.abstractmethod
    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        """Emit the result of a closed window."""
        ...

    async def emit_late_event(self, event: StreamEvent, pipeline_id: str) -> None:
        """Handle a late event (dead letter routing). Override if needed."""
        pass
