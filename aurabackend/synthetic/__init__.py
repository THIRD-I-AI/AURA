"""AURA Enterprise Synthetic Data Generator.

Schema-driven, vectorized, byte-target dataset generation to Parquet at
GB / TB / PB scale with bounded memory and a cloud-agnostic sink.
"""
from synthetic.generator import generate_chunk, generate_column
from synthetic.schema import (
    ColumnSpec,
    SizePlan,
    TableSchema,
    human_size,
    parse_size,
    plan_generation,
)
from synthetic.writer import GenerationResult, SyntheticDatasetWriter

__all__ = [
    "ColumnSpec",
    "TableSchema",
    "SizePlan",
    "parse_size",
    "human_size",
    "plan_generation",
    "generate_column",
    "generate_chunk",
    "SyntheticDatasetWriter",
    "GenerationResult",
]
