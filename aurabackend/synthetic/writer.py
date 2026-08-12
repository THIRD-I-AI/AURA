"""
Enterprise Synthetic Dataset Writer
===================================
Streams a schema-driven synthetic dataset to Parquet at GB / TB / PB
scale without ever holding more than one chunk in memory.

Design
------
* **Bounded memory.** Data is generated one ``chunk_rows`` block at a
  time and written straight into an open ``ParquetWriter`` as a row
  group. Peak RSS is ~one chunk (a few hundred MB at 1M rows), *not* the
  dataset size — this is what lets a 15 GiB box emit a 1 PB plan.
* **Byte-target calibration.** The first chunk is written, its true
  on-disk compressed size measured, and the row/file plan re-computed
  from the real bytes/row before the bulk of the data is generated.
* **Cloud-agnostic sink.** The output URI is resolved with
  ``pyarrow.fs.FileSystem.from_uri`` — ``file://`` (local), ``s3://``,
  ``gs://`` and ``abfs://`` all work with the same code path and no
  extra Python dependencies (pyarrow bundles the cloud filesystems).
* **Reproducible + parallel-ready.** Chunk ``k`` is generated from
  ``root_seed.spawn()[k]``; regenerating any file is deterministic and
  order-independent, so the same plan can be sharded across workers.

The writer is deliberately synchronous/CPU-bound; the API layer runs it
in a thread / background task and reports progress via the callback.
"""
from __future__ import annotations

import json
import os
import posixpath
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from synthetic.generator import generate_chunk
from synthetic.schema import (
    SizePlan,
    TableSchema,
    human_size,
    plan_generation,
)

ProgressCB = Callable[[Dict[str, Any]], None]


@dataclass
class GenerationResult:
    dataset: str
    output_uri: str
    files: List[str] = field(default_factory=list)
    total_rows: int = 0
    total_bytes: int = 0
    elapsed_s: float = 0.0
    plan: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data_files = [f for f in self.files if f.endswith(".parquet")]
        return {
            "dataset": self.dataset,
            "output_uri": self.output_uri,
            "n_files": len(data_files),
            "files": self.files,
            "total_rows": self.total_rows,
            "total_bytes": self.total_bytes,
            "total_human": human_size(self.total_bytes),
            "elapsed_s": round(self.elapsed_s, 3),
            "rows_per_s": int(self.total_rows / self.elapsed_s) if self.elapsed_s else 0,
            "mb_per_s": round(self.total_bytes / 1e6 / self.elapsed_s, 2) if self.elapsed_s else 0,
            "plan": self.plan,
        }


def _resolve_fs(output_uri: str):
    """Return (filesystem, base_path) for a local or cloud URI."""
    if "://" not in output_uri:
        # os.path.abspath + Path.as_uri(), NOT "file://" + posixpath.abspath:
        # posixpath treats a Windows drive path (C:\... / C:/...) as RELATIVE
        # because it only accepts a leading "/", so it joined the path onto the
        # cwd and produced //C/<cwd>/C:/... — which pyarrow then failed to
        # create with WinError 53. as_uri() also emits the drive form Windows
        # needs (file:///C:/...) and percent-encodes spaces.
        output_uri = Path(os.path.abspath(output_uri)).as_uri()
    fs, path = pafs.FileSystem.from_uri(output_uri)
    return fs, path


class SyntheticDatasetWriter:
    """Chunked, calibrated, cloud-agnostic Parquet generator."""

    def __init__(
        self,
        schema: TableSchema,
        *,
        seed: int = 0,
        compression: str = "snappy",
        chunk_rows: int = 1_000_000,
        file_target_bytes: int = 128 * 10**6,
    ):
        self.schema = schema
        self.seed = seed
        self.compression = compression
        self.chunk_rows = chunk_rows
        self.file_target_bytes = file_target_bytes
        self._root_seed = np.random.SeedSequence(seed)

    # ── planning ────────────────────────────────────────────────────
    def plan(self, target_bytes: int, measured_bpr: Optional[float] = None) -> SizePlan:
        return plan_generation(
            self.schema,
            target_bytes,
            chunk_rows=self.chunk_rows,
            file_target_bytes=self.file_target_bytes,
            measured_bytes_per_row=measured_bpr,
        )

    # ── generation ──────────────────────────────────────────────────
    def generate(
        self,
        output_uri: str,
        target_bytes: int,
        *,
        dataset_name: Optional[str] = None,
        max_files: Optional[int] = None,
        progress_cb: Optional[ProgressCB] = None,
        write_manifest: bool = True,
    ) -> GenerationResult:
        """Generate a dataset of ~``target_bytes`` at ``output_uri``.

        ``max_files`` caps output for a dry / bounded run (e.g. preview a
        1 PB plan by writing only its first few files). ``progress_cb`` is
        invoked after each file with a dict of running totals.
        """
        t0 = time.time()
        name = dataset_name or self.schema.name
        fs, base = _resolve_fs(output_uri)
        fs.create_dir(base, recursive=True)

        # Initial (uncalibrated) plan.
        plan = self.plan(target_bytes)

        # Spawn per-chunk seeds lazily as an iterator would overflow for PB
        # plans; we derive each chunk seed on demand from the root.
        result = GenerationResult(dataset=name, output_uri=output_uri, plan=plan.to_dict())

        chunk_idx = 0
        rows_written = 0
        bytes_written = 0
        file_idx = 0
        measured_bpr: Optional[float] = None

        total_rows_target = plan.total_rows
        rows_per_file = plan.rows_per_file

        while rows_written < total_rows_target:
            if max_files is not None and file_idx >= max_files:
                break

            fname = f"{name}-{file_idx:05d}.parquet"
            fpath = posixpath.join(base, fname)

            file_rows = 0
            writer: Optional[pq.ParquetWriter] = None
            with fs.open_output_stream(fpath) as sink:
                while file_rows < rows_per_file and rows_written < total_rows_target:
                    n = min(self.chunk_rows, rows_per_file - file_rows, total_rows_target - rows_written)
                    chunk_seed = self._root_seed.spawn(chunk_idx + 1)[chunk_idx]
                    table = generate_chunk(self.schema, chunk_seed, n, row_offset=rows_written)
                    if writer is None:
                        writer = pq.ParquetWriter(sink, table.schema, compression=self.compression)
                    writer.write_table(table)
                    file_rows += n
                    rows_written += n
                    chunk_idx += 1
                if writer is not None:
                    writer.close()

            finfo = fs.get_file_info(fpath)
            fsize = finfo.size or 0
            bytes_written += fsize
            result.files.append(fpath)
            file_idx += 1

            # Calibrate after the first file: measure real compressed bytes/row
            # and re-plan the remaining work.
            if measured_bpr is None and file_rows > 0:
                measured_bpr = fsize / file_rows
                plan = self.plan(target_bytes, measured_bpr=measured_bpr)
                result.plan = plan.to_dict()
                total_rows_target = plan.total_rows
                rows_per_file = plan.rows_per_file

            if progress_cb is not None:
                progress_cb({
                    "file_idx": file_idx,
                    "file": fpath,
                    "file_bytes": fsize,
                    "rows_written": rows_written,
                    "bytes_written": bytes_written,
                    "target_bytes": target_bytes,
                    "pct": round(100.0 * bytes_written / target_bytes, 2) if target_bytes else 0.0,
                    "measured_bytes_per_row": measured_bpr,
                    "elapsed_s": round(time.time() - t0, 2),
                })

        result.total_rows = rows_written
        result.total_bytes = bytes_written
        result.elapsed_s = time.time() - t0

        if write_manifest:
            manifest = {
                "dataset": name,
                "schema": self.schema.to_dict(),
                "seed": self.seed,
                "compression": self.compression,
                "result": result.to_dict(),
                "created_at": time.time(),
            }
            mpath = posixpath.join(base, "_manifest.json")
            with fs.open_output_stream(mpath) as m:
                m.write(json.dumps(manifest, indent=2).encode("utf-8"))
            result.files.append(mpath)

        return result
