"""
Base Source Adapter
====================
Abstract interface for all streaming source adapters.
Every source must implement start(), stop(), and read_batch().
"""
from __future__ import annotations

import abc
from typing import Any, Dict, List

from pipeline.streaming.models import StreamEvent


class BaseSource(abc.ABC):
    """Abstract base for streaming source adapters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialise the source connection / state."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Cleanly shut down the source."""
        ...

    @abc.abstractmethod
    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        """
        Read the next micro-batch of events.
        Returns an empty list when no data is available.
        """
        ...

    async def commit_offsets(self, offsets: Dict[str, Any]) -> None:
        """Optional: commit source offsets after checkpoint (e.g. Kafka commits)."""
        pass

    def get_offsets(self) -> Dict[str, Any]:
        """Return current source offsets for checkpoint."""
        return {}
