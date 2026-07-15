"""
Synthetic Data Schema
=====================
Declarative, JSON-friendly column specifications for the enterprise
synthetic data generator. A ``TableSchema`` is a list of ``ColumnSpec``
entries; each column names a ``dtype`` plus distribution parameters.

The vocabulary is a superset of the streaming ``SimulatedSource`` field
types, extended with real statistical distributions (normal, lognormal,
zipf, poisson), weighted categoricals, uuid/sequence keys, and a
per-column ``null_rate`` so generated data can exercise the UASR drift
detector realistically.

Everything here is pure data (no numpy import) so a schema can be built
from a UI form, serialised over the wire, and validated cheaply before a
multi-terabyte generation job is launched.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# ── Byte-width estimates for a-priori (uncompressed) sizing ─────────────
# These are the in-memory Arrow logical widths, used only for the initial
# size plan; the generator calibrates the true on-disk (compressed)
# bytes/row from the first chunk and refines the plan from there.
_APPROX_WIDTHS: Dict[str, int] = {
    "int": 8,
    "float": 8,
    "bool": 1,
    "timestamp": 8,
    "uuid": 36,
    "sequence": 8,
}

_VALID_DTYPES = {
    "int", "float", "bool", "timestamp", "uuid", "sequence", "category", "string",
}
_VALID_DISTS = {"uniform", "normal", "lognormal", "zipf", "poisson"}


@dataclass
class ColumnSpec:
    """One column of a synthetic table.

    dtype:
        int | float | bool | timestamp | uuid | sequence | category | string
    dist (numeric only):
        uniform | normal | lognormal | zipf | poisson
    """
    name: str
    dtype: str = "float"
    dist: str = "uniform"
    # numeric distribution params (used per dist)
    low: float = 0.0
    high: float = 1.0
    mean: float = 0.0
    std: float = 1.0
    lam: float = 1.0          # poisson rate / lognormal sigma helper
    zipf_a: float = 2.0       # zipf exponent (>1)
    # categorical
    categories: Optional[List[str]] = None
    weights: Optional[List[float]] = None
    # string
    prefix: str = "val_"
    str_cardinality: int = 1000
    # timestamp (epoch seconds)
    start_ts: float = 1_700_000_000.0
    end_ts: float = 1_800_000_000.0
    # shared
    null_rate: float = 0.0
    decimals: Optional[int] = None   # round floats to N decimals

    def __post_init__(self) -> None:
        if self.dtype not in _VALID_DTYPES:
            raise ValueError(f"column {self.name!r}: unknown dtype {self.dtype!r} (valid: {sorted(_VALID_DTYPES)})")
        if self.dtype in ("int", "float") and self.dist not in _VALID_DISTS:
            raise ValueError(f"column {self.name!r}: unknown dist {self.dist!r} (valid: {sorted(_VALID_DISTS)})")
        if self.dtype == "category" and not self.categories:
            raise ValueError(f"column {self.name!r}: category dtype needs non-empty 'categories'")
        if self.categories and self.weights and len(self.categories) != len(self.weights):
            raise ValueError(f"column {self.name!r}: categories/weights length mismatch")
        if not 0.0 <= self.null_rate <= 1.0:
            raise ValueError(f"column {self.name!r}: null_rate must be in [0,1]")

    def approx_width_bytes(self) -> float:
        """Uncompressed logical width estimate for a-priori sizing."""
        if self.dtype in _APPROX_WIDTHS:
            return _APPROX_WIDTHS[self.dtype]
        if self.dtype == "category":
            cats = self.categories or ["x"]
            return sum(len(c) for c in cats) / len(cats)
        if self.dtype == "string":
            return len(self.prefix) + 6  # prefix + ~6 digits
        return 8.0

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class TableSchema:
    """An ordered set of columns plus a name."""
    name: str = "synthetic"
    columns: List[ColumnSpec] = field(default_factory=list)

    def approx_bytes_per_row(self) -> float:
        return sum(c.approx_width_bytes() for c in self.columns) or 1.0

    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "columns": [c.to_dict() for c in self.columns]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TableSchema":
        cols = [ColumnSpec(**c) for c in d.get("columns", [])]
        return cls(name=d.get("name", "synthetic"), columns=cols)


# ── Size-target parsing & planning ──────────────────────────────────────
_UNITS = {
    "B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "PB": 10**15,
    "KIB": 2**10, "MIB": 2**20, "GIB": 2**30, "TIB": 2**40, "PIB": 2**50,
}


def parse_size(spec: str) -> int:
    """'1TB' / '500 GB' / '2PiB' / '1048576' → bytes (int)."""
    s = str(spec).strip().upper().replace(" ", "")
    if s.isdigit():
        return int(s)
    for unit in sorted(_UNITS, key=len, reverse=True):
        if s.endswith(unit):
            num = s[: -len(unit)]
            return int(float(num) * _UNITS[unit])
    raise ValueError(f"cannot parse size {spec!r} (use e.g. 500MB, 1TB, 2PiB)")


def human_size(nbytes: float) -> str:
    for unit in ("PB", "TB", "GB", "MB", "KB"):
        scale = _UNITS[unit]
        if nbytes >= scale:
            return f"{nbytes / scale:.2f}{unit}"
    return f"{int(nbytes)}B"


DEFAULT_COMPRESSION_RATIO = 0.35  # snappy-on-mixed a-priori guess; calibrated later


@dataclass
class SizePlan:
    """A generation plan derived from a byte target + schema."""
    target_bytes: int
    bytes_per_row: float          # on-disk (compressed) estimate
    total_rows: int
    chunk_rows: int
    n_chunks: int
    rows_per_file: int
    n_files: int
    calibrated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["target_human"] = human_size(self.target_bytes)
        d["bytes_per_row"] = round(self.bytes_per_row, 4)
        return d


def plan_generation(
    schema: TableSchema,
    target_bytes: int,
    *,
    chunk_rows: int = 1_000_000,
    file_target_bytes: int = 128 * 10**6,   # 128 MB parquet files (warehouse-friendly)
    measured_bytes_per_row: Optional[float] = None,
) -> SizePlan:
    """Compute rows/chunks/files for a byte target.

    Before any data is written we use the uncompressed logical estimate
    scaled by ``DEFAULT_COMPRESSION_RATIO``; once the generator has written
    a calibration chunk it re-plans with ``measured_bytes_per_row`` for an
    accurate file/row count.
    """
    if measured_bytes_per_row is not None and measured_bytes_per_row > 0:
        bpr = measured_bytes_per_row
        calibrated = True
    else:
        bpr = schema.approx_bytes_per_row() * DEFAULT_COMPRESSION_RATIO
        calibrated = False

    total_rows = max(1, int(target_bytes / bpr))
    chunk_rows = max(1, min(chunk_rows, total_rows))
    n_chunks = -(-total_rows // chunk_rows)  # ceil
    rows_per_file = max(chunk_rows, int(file_target_bytes / bpr))
    # round file size up to a whole number of chunks so writers never split a chunk
    rows_per_file = max(chunk_rows, (rows_per_file // chunk_rows) * chunk_rows)
    n_files = -(-total_rows // rows_per_file)
    return SizePlan(
        target_bytes=target_bytes,
        bytes_per_row=bpr,
        total_rows=total_rows,
        chunk_rows=chunk_rows,
        n_chunks=n_chunks,
        rows_per_file=rows_per_file,
        n_files=n_files,
        calibrated=calibrated,
    )
