"""
AURA Data Pipeline Engine
==========================
AI-driven data pipeline system: Prompt → Source → Process → Sink

Supports:
  - Natural language pipeline generation via LLM
  - Multiple source types: uploaded files, PostgreSQL, MySQL, BigQuery, DuckDB
  - 15+ processing operations: filter, join, aggregate, window, pivot, etc.
  - Multiple sink types: file (CSV/Parquet/JSON), PostgreSQL, DuckDB table
  - Pipeline persistence, scheduling, and re-execution
"""

from pipeline.models import (
    Pipeline,
    PipelineSource,
    PipelineSink,
    ProcessingStep,
    SourceType,
    SinkType,
    StepType,
    PipelineStatus,
    PipelineRun,
)
from pipeline.engine import PipelineEngine
from pipeline.generator import PipelineGenerator

__all__ = [
    "Pipeline",
    "PipelineSource",
    "PipelineSink",
    "ProcessingStep",
    "SourceType",
    "SinkType",
    "StepType",
    "PipelineStatus",
    "PipelineRun",
    "PipelineEngine",
    "PipelineGenerator",
]
