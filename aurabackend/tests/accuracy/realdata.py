"""Real datasets for the ADR-002 benchmark (BUG-577).

The synthetic generator (``datasets.py``) was written by the same work that wrote the
fixes, so a 0-WRONG result on it says little about a customer's data: real columns
are skewed, tied, zero-inflated, coded as floats that hold whole numbers, and carry
natural duplicate rows. These sources are real data already on disk -- the COMPAS
extract the counterfactual demo ships, and datasets bundled inside installed
packages (sklearn's ``load_*``, statsmodels' ``datasets.*.load_pandas``). Nothing here
downloads: every loader reads a file the package ships.

Each source is split per trial by deterministic row sampling into a baseline and a
batch of disjoint rows, and the batch is the ground truth -- exactly as for the
synthetic datasets, so the same injectors, classification and gates apply.

Column kinds map the real dtype onto the generator's kinds, so a fault picks the same
targets it would there: an int column is a ``count`` (a measure), a float column a
``normal`` (a measure), text is ``categorical``, a column with one value ``constant``
(no fault that rescales or retypes can change it, so none targets it).
"""
from __future__ import annotations

import functools
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from .datasets import Column, Dataset

_COMPAS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "counterfactual_service", "demo_scenarios", "data", "compas_two_year.csv")

# (package, loader) per source. Bundled sets too small to split into a baseline and
# a batch of >= 20 rows each (sklearn linnerud: 20 rows, statsmodels cpunish: 17)
# are left out: no column of theirs could even get a role (_ROLE_MIN_VALUES).
REAL_SOURCES: Dict[str, Tuple[str, str]] = {
    "compas": ("csv", _COMPAS),
    "sk_diabetes": ("sklearn", "load_diabetes"),
    "sk_wine": ("sklearn", "load_wine"),
    "sk_breast_cancer": ("sklearn", "load_breast_cancer"),
    "sk_iris": ("sklearn", "load_iris"),
    "sk_digits": ("sklearn", "load_digits"),
    "sm_macrodata": ("statsmodels", "macrodata"),
    "sm_fair": ("statsmodels", "fair"),
    "sm_grunfeld": ("statsmodels", "grunfeld"),
    "sm_star98": ("statsmodels", "star98"),
    "sm_anes96": ("statsmodels", "anes96"),
    "sm_randhie": ("statsmodels", "randhie"),
    "sm_modechoice": ("statsmodels", "modechoice"),
}


def _frame(source: str):
    kind, ref = REAL_SOURCES[source]
    if kind == "csv":
        import pandas as pd
        return pd.read_csv(ref)
    if kind == "sklearn":
        import sklearn.datasets as skd
        kw = {"scaled": False} if ref == "load_diabetes" else {}  # the raw units, not z-scores
        return getattr(skd, ref)(as_frame=True, **kw).frame
    import importlib
    return importlib.import_module(f"statsmodels.datasets.{ref}").load_pandas().data


def _plain(v: Any, dtype: str) -> Any:
    """The JSON value a pipeline would POST: int, float, str or None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if dtype == "int":
        return int(v)
    if dtype == "float":
        return float(v)
    return str(v)


@functools.lru_cache(maxsize=None)
def load(source: str) -> Tuple[Tuple[Column, ...], Tuple[Tuple[Any, ...], ...]]:
    """The source's columns and rows (as tuples, so the cache cannot be mutated)."""
    df = _frame(source)
    columns: List[Column] = []
    for name in df.columns:
        s = df[name]
        dt = str(s.dtype)
        dtype = "int" if dt.startswith(("int", "uint")) else "float" if dt.startswith("float") else "str"
        nonnull = s.dropna()
        if nonnull.nunique() <= 1:
            kind = "constant"
        else:
            kind = {"int": "count", "float": "normal", "str": "categorical"}[dtype]
        columns.append(Column(str(name), kind, float(s.isna().mean()), {"dtype": dtype}))
    rows = tuple(
        tuple(_plain(v, c.dtype) for v, c in zip(rec, columns))
        for rec in df.itertuples(index=False, name=None))
    return tuple(columns), rows


def make_real_dataset(
    source: str, seed: int, *, baseline_rows: Optional[int] = None, batch_rows: Optional[int] = None,
    row_key: Optional[str] = None,
) -> Dataset:
    """A baseline and a disjoint batch of ``source``'s rows, sampled by ``seed``.

    Sizes follow the synthetic generator (baseline 400-1200, batch 150-800) where the
    source has the rows, else 60% of it and the rest; a requested batch size is cut
    to the rows left. Raises ValueError when the source cannot give the requested
    baseline plus a batch. ``row_key`` adds a column of that name holding each row's
    position in the source, a unique key (steady-state keyed streams)."""
    cols, all_rows = load(source)
    rnd = random.Random(f"real:{source}:{seed}")
    n = len(all_rows)
    n0 = baseline_rows or min(rnd.randint(400, 1200), int(n * 0.6))
    n1 = min(batch_rows or rnd.randint(150, 800), n - n0)
    if n1 < 1:
        raise ValueError(f"{source} has {n} rows, not {n0} + {n1}")
    order = rnd.sample(range(n), n0 + n1)
    names = [c.name for c in cols]
    rows = [_row(names, all_rows, i, row_key) for i in order]
    columns = [Column(c.name, c.kind, c.null_rate, dict(c.params)) for c in cols]
    if row_key:
        columns.insert(0, Column(row_key, "id_int", 0.0, {"style": "keyed", "dtype": "int"}))
    return Dataset(seed=seed, columns=columns, baseline=rows[:n0], truth=rows[n0:], name=source,
                   baseline_positions=frozenset(order[:n0]))


def _row(names: List[str], all_rows, i: int, row_key: Optional[str]) -> Dict[str, Any]:
    row = dict(zip(names, all_rows[i]))
    return {row_key: i, **row} if row_key else row


def sample_rows(source: str, rnd: random.Random, n: int, exclude: frozenset,
                row_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """Up to ``n`` rows of ``source`` not at the positions in ``exclude`` (the baseline's),
    without replacement inside the call: a later batch of a steady-state stream."""
    cols, all_rows = load(source)
    pool = [i for i in range(len(all_rows)) if i not in exclude]
    names = [c.name for c in cols]
    return [_row(names, all_rows, i, row_key) for i in rnd.sample(pool, min(n, len(pool)))]
