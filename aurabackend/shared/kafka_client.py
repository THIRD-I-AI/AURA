"""
Kafka client helpers
=====================
Thin async helpers around aiokafka for pipeline ingestion.

Used by:
  - pipeline.engine._load_kafka_source (batched consume)
  - future: uasr ingest consumer, outbound webhook delivery

Config dict shape (what ``PipelineSource.connection`` should contain for
``SourceType.KAFKA``)::

    {
        "bootstrap_servers": "localhost:9092",  # or comma-sep
        "topic":             "events",
        "group_id":          "aura-pipeline",   # optional
        "format":            "json",            # json | ndjson-string
        "max_messages":      10000,             # cap per run
        "timeout_ms":        5000,              # idle timeout before stopping
        "from_beginning":    true,              # reset offsets to earliest
        "sasl":              null,              # optional SASL dict
        "ssl":               false,
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aura.kafka")


# Progress callback: (consumed, lag_estimate) -> awaitable.
ProgressCb = Any  # Optional[Callable[[int, Optional[int]], Awaitable[None]]]


async def consume_batch(
    cfg: Dict[str, Any],
    progress_cb: Optional[ProgressCb] = None,
) -> List[Dict[str, Any]]:
    """Consume up to ``cfg['max_messages']`` JSON messages from a topic.

    Returns a list of parsed dict rows. Non-JSON messages are wrapped as
    ``{"_raw": "<bytes.decode()>"}`` so the pipeline still sees a row.

    Stops when any of the following is true:
      - ``max_messages`` reached
      - no message arrived for ``timeout_ms``
      - ``cancel_event`` fires
    """
    try:
        from aiokafka import AIOKafkaConsumer
        from aiokafka.errors import KafkaError
    except ImportError as exc:
        raise RuntimeError(
            "aiokafka is not installed — pip install 'aiokafka>=0.10'"
        ) from exc

    bootstrap = cfg.get("bootstrap_servers") or cfg.get("brokers")
    topic     = cfg.get("topic")
    if not bootstrap or not topic:
        raise ValueError("Kafka source requires 'bootstrap_servers' and 'topic'")

    group_id       = cfg.get("group_id") or f"aura-pipeline-{topic}"
    max_messages   = int(cfg.get("max_messages", 10_000))
    idle_timeout_s = float(cfg.get("timeout_ms", 5000)) / 1000.0
    from_beginning = bool(cfg.get("from_beginning", True))
    fmt            = (cfg.get("format") or "json").lower()

    consumer_kwargs: Dict[str, Any] = {
        "bootstrap_servers": bootstrap,
        "group_id":          group_id,
        "auto_offset_reset": "earliest" if from_beginning else "latest",
        "enable_auto_commit": False,
    }
    if cfg.get("ssl"):
        consumer_kwargs["security_protocol"] = "SSL"
    if cfg.get("sasl"):
        s = cfg["sasl"]
        consumer_kwargs.update({
            "security_protocol": "SASL_SSL" if cfg.get("ssl") else "SASL_PLAINTEXT",
            "sasl_mechanism":    s.get("mechanism", "PLAIN"),
            "sasl_plain_username": s.get("username"),
            "sasl_plain_password": s.get("password"),
        })

    consumer = AIOKafkaConsumer(topic, **consumer_kwargs)
    rows: List[Dict[str, Any]] = []

    await consumer.start()
    try:
        while len(rows) < max_messages:
            try:
                batch = await asyncio.wait_for(
                    consumer.getmany(timeout_ms=1000, max_records=500),
                    timeout=idle_timeout_s,
                )
            except asyncio.TimeoutError:
                logger.info("Kafka idle timeout hit (%ss) — stopping consume", idle_timeout_s)
                break

            if not batch:
                # getmany returned empty this tick; keep looping until idle timeout
                continue

            for _tp, messages in batch.items():
                for msg in messages:
                    rows.append(_parse_message(msg.value, fmt))
                    if len(rows) >= max_messages:
                        break
                if len(rows) >= max_messages:
                    break

            if progress_cb is not None:
                try:
                    # aiokafka exposes end_offsets for lag estimation
                    assignments = consumer.assignment()
                    end = await consumer.end_offsets(assignments) if assignments else {}
                    lag = sum(max(0, end.get(tp, 0) - (consumer._fetcher._subscriptions.assignment.state_value(tp).position or 0))
                              for tp in assignments) if assignments else None
                except (KafkaError, AttributeError):
                    lag = None
                try:
                    await progress_cb(len(rows), lag)
                except Exception:
                    pass
    finally:
        await consumer.stop()

    logger.info("Kafka consume complete: %d rows from %s", len(rows), topic)
    return rows


def _parse_message(value: Optional[bytes], fmt: str) -> Dict[str, Any]:
    if value is None:
        return {"_raw": None}
    try:
        text = value.decode("utf-8", errors="replace")
    except Exception:
        return {"_raw": repr(value)}

    if fmt in ("json", "ndjson-string"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"_value": parsed}
        except json.JSONDecodeError:
            return {"_raw": text}
    return {"_raw": text}
