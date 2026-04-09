"""
Kafka Source Adapter
=====================
Consumes events from an Apache Kafka topic using aiokafka.

Config options:
  bootstrap_servers: str   – comma-separated broker list (default: "localhost:9092")
  topic:             str   – Kafka topic to consume from (required)
  group_id:          str   – consumer group id (default: "aura-streaming")
  auto_offset_reset: str   – "earliest" or "latest" (default: "latest")
  key_field:         str   – event data field to use as partition key (optional)
  max_poll_records:  int   – max records per poll (default: 100)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from pipeline.streaming.models import StreamEvent
from pipeline.streaming.sources.base import BaseSource

logger = logging.getLogger("aura.streaming.source.kafka")


class KafkaSource(BaseSource):
    """Consumes streaming events from a Kafka topic."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._consumer: Any = None
        self._topic: str = config["topic"]
        self._bootstrap_servers: str = config.get("bootstrap_servers", "localhost:9092")
        self._group_id: str = config.get("group_id", "aura-streaming")
        self._auto_offset_reset: str = config.get("auto_offset_reset", "latest")
        self._key_field: Optional[str] = config.get("key_field")
        self._max_poll_records: int = config.get("max_poll_records", 100)
        self._offsets: Dict[str, Any] = {}

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError:
            raise ImportError(
                "aiokafka is required for KafkaSource. "
                "Install it with: pip install aiokafka"
            )

        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            auto_offset_reset=self._auto_offset_reset,
            enable_auto_commit=False,  # manual commits on checkpoint
            max_poll_records=self._max_poll_records,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            "Kafka source started: topic=%s, servers=%s, group=%s",
            self._topic, self._bootstrap_servers, self._group_id,
        )

    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
        logger.info("Kafka source stopped")

    async def read_batch(self, max_events: int = 100) -> List[StreamEvent]:
        if not self._running or not self._consumer:
            return []

        events: List[StreamEvent] = []
        try:
            # Poll with a short timeout to stay non-blocking
            records = await self._consumer.getmany(
                timeout_ms=500,
                max_records=min(max_events, self._max_poll_records),
            )

            for tp, messages in records.items():
                for msg in messages:
                    data = msg.value if isinstance(msg.value, dict) else {"raw": msg.value}
                    event_time = (
                        data.get("timestamp", msg.timestamp / 1000.0)
                        if isinstance(data, dict)
                        else msg.timestamp / 1000.0
                    )

                    key = msg.key
                    if self._key_field and isinstance(data, dict):
                        key = str(data.get(self._key_field, key))

                    events.append(StreamEvent(
                        timestamp=float(event_time),
                        key=key,
                        data=data,
                        source=f"kafka://{self._topic}",
                    ))

                    # Track offsets for checkpointing
                    self._offsets[f"{tp.topic}-{tp.partition}"] = msg.offset + 1

        except Exception as e:
            logger.error("Kafka read_batch error: %s", e)

        return events

    async def commit_offsets(self, offsets: Dict[str, Any]) -> None:
        """Commit consumer offsets to Kafka on checkpoint."""
        if self._consumer:
            try:
                await self._consumer.commit()
                logger.debug("Kafka offsets committed")
            except Exception as e:
                logger.error("Kafka offset commit failed: %s", e)

    def get_offsets(self) -> Dict[str, Any]:
        return dict(self._offsets)
