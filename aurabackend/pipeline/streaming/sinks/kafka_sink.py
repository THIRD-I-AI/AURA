"""
Kafka Sink Adapter
===================
Produces processed window results to an Apache Kafka topic using aiokafka.

Config options:
  bootstrap_servers: str   – comma-separated broker list (default: "localhost:9092")
  topic:             str   – Kafka topic to produce to (required)
  key_field:         str   – field from aggregations to use as message key (optional)
  linger_ms:         int   – batch linger time in ms (default: 100)
  acks:              str   – "all", "1", or "0" (default: "all")
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from pipeline.streaming.models import StreamEvent, WindowState
from pipeline.streaming.sinks.base import BaseSink

logger = logging.getLogger("aura.streaming.sink.kafka")


class KafkaSink(BaseSink):
    """Produces closed window results to a Kafka topic."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._producer: Any = None
        self._topic: str = config["topic"]
        self._bootstrap_servers: str = config.get("bootstrap_servers", "localhost:9092")
        self._key_field: str | None = config.get("key_field")
        self._linger_ms: int = config.get("linger_ms", 100)
        self._acks: str = config.get("acks", "all")

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError:
            raise ImportError(
                "aiokafka is required for KafkaSink. "
                "Install it with: pip install aiokafka"
            )

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            acks=self._acks,
            linger_ms=self._linger_ms,
            value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        await self._producer.start()
        self._running = True
        logger.info(
            "Kafka sink started: topic=%s, servers=%s",
            self._topic, self._bootstrap_servers,
        )

    async def stop(self) -> None:
        self._running = False
        if self._producer:
            await self._producer.stop()
            self._producer = None
        logger.info("Kafka sink stopped")

    async def emit_window(self, window: WindowState, pipeline_id: str) -> None:
        if not self._producer:
            logger.warning("Kafka sink not connected — skipping emit")
            return

        payload = {
            "pipeline_id": pipeline_id,
            "window_key": window.window_key,
            "window_start": window.window_start,
            "window_end": window.window_end,
            "event_count": window.event_count,
            "aggregations": window.aggregations,
        }

        key = None
        if self._key_field and self._key_field in window.aggregations:
            key = str(window.aggregations[self._key_field])
        elif "|" in window.window_key:
            # Extract partition key from window_key format "key|start-end"
            key = window.window_key.split("|")[0]

        try:
            await self._producer.send_and_wait(self._topic, value=payload, key=key)
            logger.debug("Kafka sink: sent window %s to %s", window.window_key, self._topic)
        except Exception as e:
            logger.error("Kafka sink emit error: %s", e)

    async def emit_late_event(self, event: StreamEvent, pipeline_id: str) -> None:
        if not self._producer:
            return

        payload = {
            "type": "late_event",
            "pipeline_id": pipeline_id,
            "event_id": event.event_id,
            "key": event.key,
            "timestamp": event.timestamp,
            "data": event.data,
        }

        try:
            await self._producer.send_and_wait(
                f"{self._topic}.dead_letter",
                value=payload,
                key=event.key,
            )
        except Exception as e:
            logger.error("Kafka sink late event error: %s", e)
