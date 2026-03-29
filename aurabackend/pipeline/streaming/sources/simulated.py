"""
Simulated Source Adapter
=========================
Generates synthetic streaming events at a configurable rate.
Perfect for testing pipelines without real infrastructure.

Config options:
  event_type:        str   – label for the event stream (default: "orders")
  events_per_second: float – generation rate (default: 10)
  num_keys:          int   – number of distinct partition keys (default: 5)
  schema:            dict  – field definitions with types
                             {field_name: "int|float|string|choice:a,b,c|timestamp"}
                             Defaults to a realistic e-commerce order schema.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List

from pipeline.streaming.models import StreamEvent
from pipeline.streaming.sources.base import BaseSource

# Default schema when the user doesn't specify one
_DEFAULT_SCHEMA: Dict[str, str] = {
    "order_id": "int",
    "user_id": "int",
    "product": "choice:Laptop,Phone,Tablet,Headphones,Monitor,Keyboard,Mouse",
    "region": "choice:US-East,US-West,EU-West,EU-East,APAC",
    "amount": "float",
    "quantity": "int_small",
    "status": "choice:completed,pending,cancelled,refunded",
}


class SimulatedSource(BaseSource):
    """Generates fake streaming events at a configurable rate."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.event_type: str = config.get("event_type", "orders")
        self.events_per_second: float = config.get("events_per_second", 10.0)
        self.num_keys: int = config.get("num_keys", 5)
        self.schema: Dict[str, str] = config.get("schema", _DEFAULT_SCHEMA)
        self._keys = [f"key_{i}" for i in range(self.num_keys)]
        self._event_count = 0
        self._start_time = 0.0

    async def start(self) -> None:
        self._running = True
        self._start_time = time.time()
        self._event_count = 0

    async def stop(self) -> None:
        self._running = False

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self._running:
            return []

        # How many events to generate this batch
        batch_size = min(max_events, max(1, int(self.events_per_second)))
        events: List[StreamEvent] = []

        for _ in range(batch_size):
            self._event_count += 1
            now = time.time()
            # Occasionally generate slightly late events (5% chance)
            event_time = now - random.uniform(0, 0.5) if random.random() > 0.05 else now - random.uniform(5, 30)

            data = self._generate_row()
            events.append(StreamEvent(
                timestamp=event_time,
                key=random.choice(self._keys),
                data=data,
                source=self.event_type,
            ))

        # Throttle to match target rate
        sleep_time = batch_size / max(self.events_per_second, 0.1)
        await asyncio.sleep(sleep_time)

        return events

    def get_offsets(self) -> Dict[str, Any]:
        return {"event_count": self._event_count, "start_time": self._start_time}

    # ── Internal ──────────────────────────────────────────────────

    def _generate_row(self) -> Dict[str, Any]:
        """Generate one row according to the configured schema."""
        row: Dict[str, Any] = {}
        for field, ftype in self.schema.items():
            row[field] = self._generate_value(ftype)
        return row

    def _generate_value(self, ftype: str) -> Any:
        """Generate a random value based on the type spec."""
        if ftype == "int":
            return random.randint(1000, 99999)
        elif ftype == "int_small":
            return random.randint(1, 10)
        elif ftype == "float":
            return round(random.uniform(10.0, 500.0), 2)
        elif ftype == "string":
            return f"val_{random.randint(1, 1000)}"
        elif ftype == "timestamp":
            return time.time()
        elif ftype.startswith("choice:"):
            choices = ftype.split(":", 1)[1].split(",")
            return random.choice(choices)
        else:
            return f"unknown_{random.randint(1, 100)}"
