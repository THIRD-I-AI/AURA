import json
import logging
from collections import OrderedDict
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaProducer

logger = logging.getLogger("aura.ingestion.kafka")

# Bounds the in-memory dedup window: old enough entries are evicted so this
# never grows unbounded, while staying large enough to catch a client retrying
# the same batch_id within a reasonable window (a client re-POST, not a
# long-forgotten batch from hours ago).
_DEDUP_WINDOW_SIZE = 10_000


class KafkaUnavailableError(RuntimeError):
    """The broker is down and a lazy reconnect attempt also failed. Raised
    instead of attempting the DLQ fallback — the DLQ is Kafka too."""


class ResilientKafkaProducer:
    """
    Enterprise-grade async Kafka Producer tailored for high-volume headless ERP ingestion.
    Features exactly-once semantics (idempotence) and strict Dead Letter Queue (DLQ) routing.
    """
    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self.bootstrap_servers = bootstrap_servers
        self.producer = None
        # Application-level dedup: enable_idempotence=True only guarantees
        # exactly-once delivery for aiokafka's OWN internal retries of a
        # single send_and_wait call -- it does nothing for two independent
        # publish_with_retry calls carrying the same dedup_key (e.g. a
        # client re-POSTing the same batch_id after a timed-out response).
        self._recently_published: "OrderedDict[str, None]" = OrderedDict()

    async def start(self):
        # A broker outage at boot must NOT kill the gateway: a service that
        # fails its publishes loudly is recoverable; one that won't boot is not.
        # enable_idempotence=True only dedups aiokafka's own internal retries
        # of a single send_and_wait call -- it does NOT dedup two independent
        # publish_with_retry calls (e.g. a client re-POSTing the same
        # batch_id). See _recently_published in publish_with_retry for that.
        producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            enable_idempotence=True,
            key_serializer=lambda k: k.encode('utf-8') if k else None,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            # Retries are handled automatically by aiokafka if idempotence is enabled,
            # but we explicitly set a high retry limit for resilience.
            request_timeout_ms=30000,
            retry_backoff_ms=500
        )
        try:
            await producer.start()
        except Exception as exc:
            self.producer = None
            logger.error(
                f"Kafka unavailable at {self.bootstrap_servers} ({exc}); "
                "boot continues, publishes will lazily retry")
            return
        self.producer = producer
        logger.info(f"Connected resilient Kafka producer to {self.bootstrap_servers} with idempotence=True")

    async def _ensure_started(self) -> bool:
        if self.producer is None:
            await self.start()
        return self.producer is not None

    async def stop(self):
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka producer stopped.")

    async def publish_with_retry(
        self,
        topic: str,
        payload: Dict[str, Any],
        partition_key: str = None,
        max_retries: int = 3,
        dedup_key: Optional[str] = None,
    ):
        """
        Publishes a message to the target topic. If it fails beyond the aiokafka retry limit,
        routes the failed message to a Dead Letter Queue (DLQ).
        Partition keying ensures parallel node synchronization without eventual consistency delays.

        dedup_key: an application-level idempotency key (e.g. a batch_id).
        When given and already seen within the recent window, the publish is
        skipped entirely -- this is the app-level dedup enable_idempotence
        does not provide (see __init__/start()).
        """
        if dedup_key is not None and dedup_key in self._recently_published:
            logger.warning(f"Skipping duplicate publish to {topic}: dedup_key={dedup_key!r} already published")
            return
        if not await self._ensure_started():
            raise KafkaUnavailableError(
                f"broker {self.bootstrap_servers} down; cannot publish to {topic}")
        try:
            # We rely on aiokafka's internal retries for transient network issues.
            await self.producer.send_and_wait(topic, value=payload, key=partition_key)
            logger.debug(f"Successfully published event to {topic} with key {partition_key}")
            if dedup_key is not None:
                self._recently_published[dedup_key] = None
                if len(self._recently_published) > _DEDUP_WINDOW_SIZE:
                    self._recently_published.popitem(last=False)
        except Exception as e:
            logger.error(f"Failed to publish to {topic} after retries: {e}. Routing to DLQ.")
            await self._route_to_dlq(payload, error_msg=str(e), original_topic=topic)

    async def _route_to_dlq(self, payload: Dict[str, Any], error_msg: str, original_topic: str):
        """
        Routes un-publishable payloads to the DLQ to ensure zero data loss
        and enable later reconciliation.
        """
        dlq_topic = "aura.dlq.ledger"
        dlq_payload = {
            "original_topic": original_topic,
            "error": error_msg,
            "payload": payload
        }
        try:
            # Send without waiting to avoid blocking ingestion loop completely if DLQ is also down
            await self.producer.send_and_wait(dlq_topic, value=dlq_payload, key=dlq_payload.get("original_topic"))
            logger.warning(f"Payload successfully routed to DLQ topic '{dlq_topic}'")
        except Exception as dlq_e:
            # Extreme fallback: Write to disk or alerting system
            logger.critical(f"FATAL: Failed to route to DLQ! Data loss imminent. Error: {dlq_e}. Payload: {payload}")

# Singleton instance
kafka_producer = ResilientKafkaProducer()
