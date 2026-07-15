"""
Vectorized Column Generator
===========================
Turns a ``ColumnSpec`` into an Arrow array of ``n`` rows using numpy,
fully vectorized (no per-row Python loop) so we can sustain millions of
rows/second toward TB/PB targets.

Reproducibility contract
-------------------------
All randomness derives from a ``numpy.random.SeedSequence``. The engine
spawns one **independent** child sequence per (chunk_index) so any chunk
can be regenerated in isolation — this is what makes parallel and
distributed generation deterministic: worker k generates chunk k from
``root_seed.spawn()[k]`` and gets byte-identical output regardless of
order or concurrency.
"""
from __future__ import annotations

import uuid as _uuid
from typing import List

import numpy as np
import pyarrow as pa

from synthetic.schema import ColumnSpec, TableSchema


def _apply_nulls(values: np.ndarray, mask_rng: np.random.Generator, null_rate: float):
    """Return a validity mask (True = valid) or None when no nulls requested."""
    if null_rate <= 0.0:
        return None
    valid = mask_rng.random(len(values)) >= null_rate
    return valid


def _gen_numeric(spec: ColumnSpec, rng: np.random.Generator, n: int) -> np.ndarray:
    d = spec.dist
    if d == "uniform":
        out = rng.uniform(spec.low, spec.high, n)
    elif d == "normal":
        out = rng.normal(spec.mean, spec.std, n)
    elif d == "lognormal":
        out = rng.lognormal(spec.mean, max(spec.std, 1e-9), n)
    elif d == "zipf":
        out = rng.zipf(max(spec.zipf_a, 1.0001), n).astype("float64")
    elif d == "poisson":
        out = rng.poisson(max(spec.lam, 0.0), n).astype("float64")
    else:  # pragma: no cover — validated upstream
        out = rng.uniform(spec.low, spec.high, n)
    return out


def generate_column(
    spec: ColumnSpec,
    rng: np.random.Generator,
    n: int,
    *,
    row_offset: int = 0,
) -> pa.Array:
    """Generate ``n`` values for one column as an Arrow array.

    ``row_offset`` is the global index of the first row in this chunk,
    used by ``sequence`` columns so ids stay globally monotonic across
    chunks and files.
    """
    dt = spec.dtype

    if dt == "sequence":
        arr = np.arange(row_offset, row_offset + n, dtype="int64")
        return pa.array(arr, type=pa.int64())

    if dt == "uuid":
        # Vectorized-ish: draw 128-bit ints from the rng, format as uuid.
        hi = rng.integers(0, 2**63 - 1, n, dtype="int64")
        lo = rng.integers(0, 2**63 - 1, n, dtype="int64")
        vals = [str(_uuid.UUID(int=((int(h) << 64) | int(lw)) & ((1 << 128) - 1))) for h, lw in zip(hi, lo)]
        return pa.array(vals, type=pa.string())

    if dt == "bool":
        vals = rng.random(n) < 0.5
        valid = _apply_nulls(vals, rng, spec.null_rate)
        return pa.array(vals, type=pa.bool_(), mask=None if valid is None else ~valid)

    if dt == "timestamp":
        secs = rng.uniform(spec.start_ts, spec.end_ts, n)
        us = (secs * 1_000_000).astype("int64")
        valid = _apply_nulls(us, rng, spec.null_rate)
        return pa.array(us, type=pa.timestamp("us"), mask=None if valid is None else ~valid)

    if dt == "category":
        cats = spec.categories or ["x"]
        p = None
        if spec.weights:
            w = np.asarray(spec.weights, dtype="float64")
            p = w / w.sum()
        idx = rng.choice(len(cats), size=n, p=p)
        vals = np.asarray(cats, dtype=object)[idx]
        valid = _apply_nulls(idx, rng, spec.null_rate)
        return pa.array(vals.tolist(), type=pa.string(), mask=None if valid is None else ~valid)

    if dt == "string":
        ids = rng.integers(0, max(spec.str_cardinality, 1), n)
        vals = [f"{spec.prefix}{int(i)}" for i in ids]
        valid = _apply_nulls(ids, rng, spec.null_rate)
        return pa.array(vals, type=pa.string(), mask=None if valid is None else ~valid)

    # numeric: int | float
    out = _gen_numeric(spec, rng, n)
    if dt == "int":
        arr = np.rint(out).astype("int64")
        valid = _apply_nulls(arr, rng, spec.null_rate)
        return pa.array(arr, type=pa.int64(), mask=None if valid is None else ~valid)
    else:
        if spec.decimals is not None:
            out = np.round(out, spec.decimals)
        valid = _apply_nulls(out, rng, spec.null_rate)
        return pa.array(out, type=pa.float64(), mask=None if valid is None else ~valid)


def generate_chunk(
    schema: TableSchema,
    seed_seq: np.random.SeedSequence,
    n: int,
    *,
    row_offset: int = 0,
) -> pa.Table:
    """Generate an ``n``-row Arrow table for the whole schema.

    Each column gets its own child RNG spawned from ``seed_seq`` so adding
    or reordering columns doesn't perturb other columns' streams.
    """
    child_seeds = seed_seq.spawn(len(schema.columns))
    arrays: List[pa.Array] = []
    for col_spec, cseed in zip(schema.columns, child_seeds):
        rng = np.random.default_rng(cseed)
        arrays.append(generate_column(col_spec, rng, n, row_offset=row_offset))
    return pa.table(arrays, names=schema.column_names())
