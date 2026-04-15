"""
Streaming Sink Adapters
========================
Pluggable sinks that receive processed window results:
  - SSESink:      pushes to Server-Sent Events channels (real-time frontend)
  - ConsoleSink:  logs to stdout (debugging)
  - DatabaseSink: writes to PostgreSQL/DuckDB
  - FileSink:     micro-batch writes (Parquet/CSV)
  - AlertSink:    triggers when conditions are met
"""

from pipeline.streaming.sinks.alert_sink import AlertSink
from pipeline.streaming.sinks.base import BaseSink
from pipeline.streaming.sinks.console_sink import ConsoleSink
from pipeline.streaming.sinks.database_sink import DatabaseSink
from pipeline.streaming.sinks.file_sink import FileSink
from pipeline.streaming.sinks.kafka_sink import KafkaSink
from pipeline.streaming.sinks.sse_sink import SSESink
from pipeline.streaming.sinks.webhook_sink import WebhookSink

__all__ = [
    "BaseSink",
    "SSESink",
    "ConsoleSink",
    "DatabaseSink",
    "FileSink",
    "AlertSink",
    "KafkaSink",
    "WebhookSink",
]
