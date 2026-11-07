"""Fault injectors with exact ground truth (ADR-002 benchmark).

Each injector corrupts ``Trial.sent`` (what upstream delivers) and records, row by
row, what a correct healer must hand the destination in ``Trial.expected``:

* a repairable fault (rename, lossless retype, x10**n, int-as-float, new column)
  leaves ``expected`` at the clean truth -- the healer has to restore it exactly;
* an unrepairable change (a removed column, a x7 or x2.5 decoy, a value that does
  not cast back) sets the changed rows to ``None``: no value is correct for them,
  so they must be quarantined (ADR-002: never guess);
* a detection-only fault (null spike, duplicates, volume drop) has nothing to
  repair, so ``expected`` follows ``sent`` and the fault must be raised as an alert
  of ``required_alerts`` with ``healed=false`` (ADR-002 "Detection only").

Faults touch distinct columns, so two of them compose into a combination trial with
the same bookkeeping. Every trial is rebuilt from its seed alone.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .datasets import (
    INT_KINDS,
    MEASURE_KINDS,
    Column,
    Dataset,
    column_params,
    copy_rows,
    make_dataset,
    sample,
    strict_equal,
)


class NotApplicable(Exception):
    """The dataset has no column this fault can hit."""


@dataclass
class Trial:
    fault: str
    seed: int
    dataset: Dataset
    sent: List[Dict[str, Any]]
    expected: List[Optional[Dict[str, Any]]]
    snapshot: Optional[Dict[str, str]]
    tenant: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    baseline_metadata: Dict[str, Any] = field(default_factory=dict)
    required_alerts: Set[str] = field(default_factory=set)
    expect_healed: bool = True
    used: Set[str] = field(default_factory=set)
    details: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)

    @property
    def detail(self) -> str:
        snap = "snapshot" if self.snapshot is not None else "no snapshot"
        return "; ".join(self.details) + f" [{snap}, {len(self.sent)} rows]"

    @property
    def case_class(self) -> str:
        """The trial's fault without its seed-specific names and counts, for grouping."""
        snap = "snapshot" if self.snapshot is not None else "no snapshot"
        return " + ".join(self.classes) + f" [{snap}; {self.dataset.shape}]"

    def note(self, detail: str, case_class: str) -> None:
        self.details.append(detail)
        self.classes.append(case_class)

    def pick(self, rnd: random.Random, kinds) -> Column:
        cols = [c for c in self.dataset.columns if c.kind in kinds and c.name not in self.used]
        if not cols:
            raise NotApplicable(kinds)
        col = rnd.choice(cols)
        self.used.add(col.name)
        return col

    def map_column(self, name: str, fn: Callable[[Any], Any]) -> int:
        """Apply ``fn`` to every non-null value of ``name`` in ``sent``; returns how many changed."""
        changed = 0
        for row in self.sent:
            v = row.get(name)
            if v is not None:
                new = fn(v)
                changed += not strict_equal(new, v)
                row[name] = new
        return changed

    def void_changed(self, name: str, original: List[Any]) -> None:
        """Rows whose ``name`` no longer holds the truth have no correct output."""
        for i, row in enumerate(self.sent):
            if self.expected[i] is not None and not strict_equal(row.get(name), original[i]):
                self.expected[i] = None

    def set_type(self, name: str, dtype: str) -> None:
        if self.snapshot is not None:
            self.snapshot[name] = dtype


# ── Injectors ──────────────────────────────────────────────────────────────────

def _rename(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, [c.kind for c in t.dataset.columns])
    if rnd.random() < 0.5:
        # Same name once case and separators are ignored (ADR-002 "normalised name").
        new = rnd.choice([col.name.upper(), col.name.title().replace("_", ""), col.name.replace("_", "-"),
                          col.name.title()])
        rule = "normalised"
    else:
        new = rnd.choice(["col_a7", "field_3", "x_value", "attr_new", "c12"])
        rule = "opaque"
    if new == col.name:
        new = col.name + "_v2"
        rule = "opaque"
    for row in t.sent:
        row[new] = row.pop(col.name)
    if t.snapshot is not None:
        t.snapshot[new] = t.snapshot.pop(col.name)
    t.note(f"rename {col.name}->{new} ({col.kind})", f"rename {rule} ({col.kind})")


def _to_text(v: Any) -> str:
    # The text an upstream export writes: shortest round-trip float text, lowercase bools.
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v) if isinstance(v, float) else str(v)


def _retype_lossless(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, MEASURE_KINDS + ("id_int", "bool"))
    t.map_column(col.name, _to_text)
    t.set_type(col.name, "str")
    t.note(f"retype to string, lossless, on {col.name} ({col.kind})", f"retype lossless ({col.kind})")


_LOSSY = {
    "int": ["007", "+5", "4.0", "1,234", "n/a", "-", "12 units"],
    "float": ["n/a", "N/A", "1,234.5", "12.5 USD", "-", "?", "1e"],
    "bool": ["yes", "no", "1", "0", "Y", "n/a"],
}


def _retype_lossy(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, MEASURE_KINDS + ("id_int", "bool"))
    original = [r.get(col.name) for r in t.sent]
    t.map_column(col.name, _to_text)
    present = [i for i, v in enumerate(original) if v is not None]
    k = max(1, int(len(present) * rnd.uniform(0.005, 0.08)))
    for i in rnd.sample(present, min(k, len(present))):
        t.sent[i][col.name] = rnd.choice(_LOSSY[col.dtype])
        t.expected[i] = None  # no lossless cast back exists: the row must be held aside
    t.set_type(col.name, "str")
    t.note(f"retype to string, {k} lossy values, on {col.name} ({col.kind})", f"retype lossy ({col.kind})")


def _scale(power: int) -> Callable[[Any], Any]:
    factor = 10 ** abs(power)
    # Upstream multiplies for a larger unit's inverse and divides for a smaller one,
    # exactly as a unit bug would: v * 1000, or v / 1000 (BUG-571 is the latter).
    return (lambda v: v * factor) if power > 0 else (lambda v: v / factor)


def _scale_fault(t: Trial, rnd: random.Random, powers: List[int]) -> None:
    col = t.pick(rnd, MEASURE_KINDS)
    power = rnd.choice(powers)
    t.map_column(col.name, _scale(power))
    if col.dtype == "int" and power < 0:
        t.set_type(col.name, "float")
    t.note(f"scale x10^{power} on {col.name} ({col.kind})", f"scale x10^{power} ({col.kind})")


def _decoy(factor: float) -> Callable[[Trial, random.Random], None]:
    def inject(t: Trial, rnd: random.Random) -> None:
        col = t.pick(rnd, MEASURE_KINDS)
        original = [r.get(col.name) for r in t.sent]
        t.map_column(col.name, lambda v: v * factor)
        if col.dtype == "int" and not float(factor).is_integer():
            t.set_type(col.name, "float")
        # No power of ten explains x7 or x2.5: any value returned for a changed row is wrong.
        t.void_changed(col.name, original)
        t.note(f"decoy x{factor:g} on {col.name} ({col.kind})", f"decoy x{factor:g} ({col.kind})")
    return inject


def _int_as_float(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, INT_KINDS)
    t.map_column(col.name, float)
    t.set_type(col.name, "float")
    t.note(f"int as float on {col.name} ({col.kind})", f"int as float ({col.kind})")


def _new_column(t: Trial, rnd: random.Random) -> None:
    kind = rnd.choice(["categorical", "uniform", "count", "bool", "date"])
    name = rnd.choice(["coupon_code", "new_metric", "region_v2", "promo_flag", "batch_note"])
    extra = Column(name, kind, rnd.choice([0.0, 0.05, 0.5]), column_params(kind, rnd))
    values = sample([extra], len(t.sent), rnd, 0)
    for i, row in enumerate(t.sent):
        row[name] = values[i][name]
        if t.expected[i] is not None:
            # ADR-002 "New column": kept, the schema evolves, so the truth gains it.
            t.expected[i] = {**t.expected[i], name: values[i][name]}
    t.set_type(name, extra.dtype)
    t.used.add(name)
    t.note(f"new column {name} ({kind})", f"new column ({kind})")


def _removed_column(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, [c.kind for c in t.dataset.columns if not c.kind.startswith("id_")])
    for row in t.sent:
        row.pop(col.name)
    if t.snapshot is not None:
        t.snapshot.pop(col.name)
    # The column's values are gone: no row can be restored without a guess.
    t.expected = [None] * len(t.sent)
    t.note(f"removed column {col.name} ({col.kind})", f"removed column ({col.kind})")


def _null_spike(t: Trial, rnd: random.Random) -> None:
    col = t.pick(rnd, [c.kind for c in t.dataset.columns if not c.kind.startswith("id_")])
    p = rnd.choice([0.3, 0.5, 0.8, 1.0])
    for i, row in enumerate(t.sent):
        if row.get(col.name) is not None and rnd.random() < p:
            row[col.name] = None
            if t.expected[i] is not None:
                t.expected[i] = {**t.expected[i], col.name: None}
    t.required_alerts.add("null_rate")
    t.expect_healed = False
    t.note(f"null spike {int(p * 100)}% on {col.name} ({col.kind})", f"null spike {int(p * 100)}% ({col.kind})")


def _duplicates(t: Trial, rnd: random.Random) -> None:
    n = len(t.sent)
    frac = rnd.choice([0.05, 0.1, 0.2, 0.3])
    picks = [rnd.randrange(n) for _ in range(max(1, int(n * frac)))]
    pairs = list(zip(t.sent, t.expected)) + [(dict(t.sent[i]), copy_rows([t.expected[i]])[0]) for i in picks]
    rnd.shuffle(pairs)
    t.sent = [s for s, _ in pairs]
    t.expected = [e for _, e in pairs]
    t.required_alerts.add("duplicate")
    t.expect_healed = False
    t.note(f"duplicates, {len(picks)} exact copies", f"duplicates {int(frac * 100)}%")


def _volume_drop(t: Trial, rnd: random.Random) -> None:
    n0 = len(t.dataset.baseline)
    n1 = rnd.randint(1, max(1, n0 // 101))
    t.sent, t.expected = t.sent[:n1], t.expected[:n1]
    t.metadata["complete_run"] = True
    t.baseline_metadata["complete_run"] = True
    t.required_alerts.add("volume")
    t.expect_healed = False
    t.note(f"volume drop {n0}->{n1} rows, complete runs", "volume drop")


INJECTORS: Dict[str, Callable[[Trial, random.Random], None]] = {
    "rename": _rename,
    "retype_lossless": _retype_lossless,
    "retype_lossy": _retype_lossy,
    "scale_up": lambda t, r: _scale_fault(t, r, [1, 2, 3, 4, 5, 6]),
    "scale_down": lambda t, r: _scale_fault(t, r, [-1, -2, -3, -4, -5, -6]),
    "decoy_x7": _decoy(7),
    "decoy_x2.5": _decoy(2.5),
    "int_as_float": _int_as_float,
    "new_column": _new_column,
    "removed_column": _removed_column,
    "null_spike": _null_spike,
    "duplicates": _duplicates,
    "volume_drop": _volume_drop,
}

# Pairs on distinct columns; a row-count fault always goes last so the row-level
# bookkeeping of the first fault is copied with the rows.
COMBOS = [
    ("rename", "scale_up"),
    ("rename", "new_column"),
    ("rename", "duplicates"),
    ("retype_lossless", "scale_down"),
    ("retype_lossless", "rename"),
    ("new_column", "scale_up"),
    ("int_as_float", "new_column"),
    ("scale_up", "null_spike"),
    ("scale_down", "scale_up"),
    ("removed_column", "new_column"),
    ("null_spike", "duplicates"),
    ("decoy_x7", "rename"),
]

FAULT_TYPES = list(INJECTORS) + ["combo"]


def build_trial(fault: str, seed: int) -> Trial:
    """The trial for ``fault`` at ``seed``, or NotApplicable after a few shapes."""
    rnd = random.Random(f"{fault}:{seed}")
    for attempt in range(20):
        kw = {"baseline_rows": rnd.randint(1000, 2000)} if fault == "volume_drop" else {}
        ds = make_dataset(rnd.randrange(1 << 30), **kw)
        steps = list(rnd.choice(COMBOS)) if fault == "combo" else [fault]
        t = Trial(
            fault=fault, seed=seed, dataset=ds,
            sent=copy_rows(ds.truth), expected=copy_rows(ds.truth),
            snapshot=dict(ds.schema) if rnd.random() < 0.5 else None,
            tenant=rnd.choice([None, "tenant_a", "tenant_b"]),
        )
        try:
            for step in steps:
                INJECTORS[step](t, rnd)
        except NotApplicable:
            continue
        return t
    raise NotApplicable(fault)


def build_clean(seed: int) -> Trial:
    """A clean batch of the dataset's own process, at one of several sizes, with or
    without a snapshot, sometimes as a complete run against a complete-run baseline."""
    rnd = random.Random(f"clean:{seed}")
    size = rnd.choice(["tiny", "small", "medium", "large"])
    n1 = {"tiny": rnd.randint(20, 60), "small": rnd.randint(60, 200),
          "medium": rnd.randint(200, 900), "large": rnd.randint(1500, 4000)}[size]
    ds = make_dataset(rnd.randrange(1 << 30), batch_rows=n1)
    t = Trial(
        fault="clean", seed=seed, dataset=ds,
        sent=copy_rows(ds.truth), expected=copy_rows(ds.truth),
        snapshot=dict(ds.schema) if rnd.random() < 0.5 else None,
        tenant=rnd.choice([None, "tenant_a", "tenant_b"]),
    )
    if size in ("medium", "large") and rnd.random() < 0.5:
        t.metadata["complete_run"] = True
        t.baseline_metadata["complete_run"] = True
    t.note(f"clean {size}", f"clean {size}" + (" complete run" if t.metadata else ""))
    return t
