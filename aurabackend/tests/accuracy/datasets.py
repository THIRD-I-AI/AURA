"""Deterministic clean tabular datasets with known ground truth (ADR-002 benchmark).

A ``Dataset`` is a schema plus two samples of the same process: a baseline the source
is registered with, and a batch that is the ground truth -- what the destination
should receive when nothing goes wrong. Faults (``faults.py``) are injected into a
copy of the batch, so the truth is never in doubt.

Everything is drawn from ``random.Random(seed)``: the same seed gives the same rows
on every platform and Python version, so a failing trial can be replayed by seed.
Values are plain JSON types (int, float, str, bool, None), as a pipeline POSTs them.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Kinds a unit/scale fault can hit: measures, never identifiers, labels or dates.
MEASURE_KINDS = ("normal", "uniform", "lognormal", "count", "centred", "money")
INT_KINDS = ("id_int", "count")
NUMERIC_KINDS = MEASURE_KINDS + ("id_int",)

_NAMES = {
    "id_int": ["customer_id", "order_id", "account_id", "user_id"],
    "id_str": ["order_ref", "invoice_no", "sku", "ticket_key"],
    "normal": ["latency_ms", "temperature_c", "height_cm", "response_time"],
    "uniform": ["amount", "discount_pct", "load_factor", "score"],
    "lognormal": ["revenue", "file_size_kb", "session_seconds", "basket_value"],
    "count": ["quantity", "page_views", "retries", "items"],
    "centred": ["delta", "pnl_change", "error_offset", "residual"],
    "money": ["price", "unit_cost", "fee", "balance"],
    "categorical": ["status", "region", "channel", "tier"],
    "date": ["created_on", "ship_date", "event_day", "signup_date"],
    "bool": ["is_active", "is_refund", "opted_in", "flagged"],
}
SCHEMA_TYPE = {
    "id_int": "int", "id_str": "str", "normal": "float", "uniform": "float",
    "lognormal": "float", "count": "int", "centred": "float", "money": "float",
    "categorical": "str", "date": "str", "bool": "bool",
}
_KEY_SPACE = 1_000_003  # prime
_CATEGORIES = [
    ["active", "inactive", "pending"],
    ["eu", "us", "apac", "latam", "mea"],
    ["web", "mobile", "store", "partner", "phone", "email"],
    ["free", "basic", "pro", "enterprise"],
]


@dataclass
class Column:
    name: str
    kind: str
    null_rate: float
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def dtype(self) -> str:
        return SCHEMA_TYPE[self.kind]


@dataclass
class Dataset:
    seed: int
    columns: List[Column]
    baseline: List[Dict[str, Any]]
    truth: List[Dict[str, Any]]

    @property
    def schema(self) -> Dict[str, str]:
        return {c.name: c.dtype for c in self.columns}

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.name == name)

    def of_kind(self, *kinds: str) -> List[Column]:
        return [c for c in self.columns if c.kind in kinds]

    @property
    def shape(self) -> str:
        """The identifier style and whether a date column is present, for grouping results."""
        ident = self.columns[0]
        tag = "text id" if ident.kind == "id_str" else f"{ident.params['style']} int id"
        return tag + (" + date" if any(c.kind == "date" for c in self.columns) else "")


def column_params(kind: str, rnd: random.Random) -> Dict[str, Any]:
    if kind == "normal":
        mu = rnd.uniform(20.0, 5000.0)
        return {"mu": mu, "sigma": mu * rnd.uniform(0.05, 0.3), "digits": rnd.choice([1, 2, 3])}
    if kind == "uniform":
        lo = rnd.uniform(0.0, 500.0)
        return {"lo": lo, "hi": lo + rnd.uniform(10.0, 2000.0), "digits": rnd.choice([2, 3])}
    if kind == "lognormal":
        return {"mu": rnd.uniform(1.0, 7.0), "sigma": rnd.uniform(0.3, 1.2), "digits": 2}
    if kind == "count":
        return {"mean": rnd.uniform(2.0, 400.0)}
    if kind == "centred":
        return {"sigma": rnd.uniform(0.5, 50.0), "digits": rnd.choice([2, 3])}
    if kind == "money":
        return {"mu": rnd.uniform(2.0, 6.0), "sigma": rnd.uniform(0.2, 0.8)}
    if kind == "categorical":
        cats = list(rnd.choice(_CATEGORIES))
        return {"cats": cats, "weights": [rnd.uniform(0.5, 3.0) for _ in cats]}
    if kind == "date":
        return {"start": _dt.date(2024, 1, 1).toordinal() + rnd.randint(0, 600), "span": rnd.randint(30, 365)}
    if kind == "bool":
        return {"p": rnd.uniform(0.15, 0.85)}
    if kind == "id_int":
        # Sequential: an auto-increment key, so every new batch lies above the
        # baseline's range. Keyed: a unique key spread over a fixed population
        # (a customer id), so baseline and batch come from the same distribution.
        return {"style": rnd.choice(["sequential", "keyed"]), "start": rnd.randint(1, 900_000),
                "stride": rnd.randrange(1, _KEY_SPACE)}
    if kind == "id_str":
        return {"prefix": rnd.choice(["ORD", "INV", "SKU", "TCK"])}
    raise ValueError(kind)


def _poisson(rnd: random.Random, lam: float) -> int:
    # Normal approximation above 30 keeps generation O(1) per value; counts stay >= 0.
    if lam > 30:
        return max(0, int(round(rnd.gauss(lam, math.sqrt(lam)))))
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rnd.random()
        if p <= threshold:
            return k
        k += 1


def _value(col: Column, rnd: random.Random, ordinal: int) -> Any:
    p, kind = col.params, col.kind
    if kind == "id_int":
        if p["style"] == "sequential":
            return p["start"] + ordinal
        # An affine map mod a prime is a bijection, so keys stay unique.
        return 1 + (p["start"] + p["stride"] * ordinal) % _KEY_SPACE
    if kind == "id_str":
        return f"{p['prefix']}-{ordinal:07d}"
    if col.null_rate and rnd.random() < col.null_rate:
        return None
    if kind == "normal":
        return round(rnd.gauss(p["mu"], p["sigma"]), p["digits"])
    if kind == "uniform":
        return round(rnd.uniform(p["lo"], p["hi"]), p["digits"])
    if kind == "lognormal":
        return round(rnd.lognormvariate(p["mu"], p["sigma"]), p["digits"])
    if kind == "count":
        return _poisson(rnd, p["mean"])
    if kind == "centred":
        return round(rnd.gauss(0.0, p["sigma"]), p["digits"])
    if kind == "money":
        return round(rnd.lognormvariate(p["mu"], p["sigma"]), 2)
    if kind == "categorical":
        return rnd.choices(p["cats"], weights=p["weights"])[0]
    if kind == "date":
        return _dt.date.fromordinal(p["start"] + rnd.randrange(p["span"])).isoformat()
    if kind == "bool":
        return rnd.random() < p["p"]
    raise ValueError(kind)


def sample(columns: List[Column], n: int, rnd: random.Random, first_ordinal: int) -> List[Dict[str, Any]]:
    """``n`` rows of the dataset's process. Ordinals keep identifiers unique, so a
    clean batch never holds an exact duplicate row by chance."""
    return [{c.name: _value(c, rnd, first_ordinal + i) for c in columns} for i in range(n)]


def make_dataset(
    seed: int,
    *,
    baseline_rows: Optional[int] = None,
    batch_rows: Optional[int] = None,
    require: Callable[[List[Column]], bool] = lambda cols: True,
) -> Dataset:
    """A random shape (4-9 columns: one identifier, at least two measures, then labels,
    dates, booleans and more measures), drawn until ``require`` accepts it so a fault
    always has a column it applies to."""
    rnd = random.Random(seed)
    while True:
        kinds = [rnd.choice(["id_int", "id_int", "id_str"])]
        kinds += rnd.sample(list(MEASURE_KINDS), 2)
        kinds += [rnd.choice(["categorical", "date", "bool", *MEASURE_KINDS]) for _ in range(rnd.randint(1, 6))]
        columns: List[Column] = []
        used: set = set()
        for kind in kinds:
            free = [n for n in _NAMES[kind] if n not in used]
            if not free:
                continue
            name = rnd.choice(free)
            used.add(name)
            null_rate = 0.0 if kind.startswith("id_") else rnd.choice([0.0, 0.0, 0.01, 0.03])
            columns.append(Column(name, kind, null_rate, column_params(kind, rnd)))
        if require(columns):
            break
    n0 = baseline_rows or rnd.randint(400, 1200)
    n1 = batch_rows or rnd.randint(150, 800)
    baseline = sample(columns, n0, rnd, 0)
    truth = sample(columns, n1, rnd, n0)
    return Dataset(seed=seed, columns=columns, baseline=baseline, truth=truth)


def copy_rows(rows: List[Optional[Dict[str, Any]]]) -> List[Optional[Dict[str, Any]]]:
    """Rows hold only immutable scalars, so a copy of each dict is a full copy."""
    return [dict(r) if r is not None else None for r in rows]


def strict_equal(a: Any, b: Any) -> bool:
    """Equality that also requires the same JSON type: 5.0 is not 5, True is not 1.

    Plain ``==`` would score an integer id returned as 5.0 (BUG-572) as correct."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b
