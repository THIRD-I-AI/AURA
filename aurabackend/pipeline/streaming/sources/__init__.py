"""
Streaming Source Adapters
==========================
Pluggable sources that yield StreamEvent objects:
  - SimulatedSource: generates fake events for testing/demo
  - FileWatcherSource: watches a directory for new files
  - KafkaSource: consumes from Kafka topics (optional aiokafka)
  - CDCSource: PostgreSQL logical replication (optional psycopg2)
"""

from pipeline.streaming.sources.base import BaseSource
from pipeline.streaming.sources.file_watcher import FileWatcherSource
from pipeline.streaming.sources.kafka_source import KafkaSource
from pipeline.streaming.sources.simulated import SimulatedSource
from pipeline.streaming.sources.websocket_source import WebSocketSource

__all__ = [
    "BaseSource",
    "SimulatedSource",
    "FileWatcherSource",
    "KafkaSource",
    "WebSocketSource",
]
