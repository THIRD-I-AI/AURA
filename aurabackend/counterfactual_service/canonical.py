"""
Canonical JSON serialisation for the Counterfactual Audit Engine.

Determinism rules (every rule is a hash-reproducibility contract):

* Dict keys sorted recursively.
* Lists preserve their order. Lists are *ordered data*; sorting them
  silently would change semantics.
* Floats serialise as 6-decimal-fixed strings. Two IEEE-754 values that
  round to the same six-decimal print are treated as the same value
  for hashing purposes — this is the right behaviour because DoWhy's
  estimators return numbers with platform-dependent low-order bits.
* Datetimes serialise as ISO-8601 UTC with explicit ``Z`` suffix.
  Naive datetimes are interpreted as UTC. Aware non-UTC datetimes are
  converted.
* ``None``-valued keys are dropped before serialisation. Absence is not
  representable as null in this format. Callers that need to record a
  null-ish field must use a sentinel string.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


class _Omit:
    """Marker returned by _normalize when a value should be dropped."""


_OMIT = _Omit()


def _normalize(value: Any) -> Any:
    if value is None:
        return _OMIT
    if isinstance(value, bool):
        # bool is a subclass of int; handle it explicitly so it survives
        # the float branch unmodified.
        return value
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    if isinstance(value, dict):
        out = {}
        for k in sorted(value.keys(), key=str):
            v = _normalize(value[k])
            if v is _OMIT:
                continue
            out[str(k)] = v
        return out
    if isinstance(value, (list, tuple)):
        return [n for n in (_normalize(v) for v in value) if n is not _OMIT]
    return value


def canonical_dumps(value: Any) -> str:
    """Return the canonical JSON serialisation of ``value``.

    Output is compact (no whitespace), keys sorted recursively, floats
    rounded to six decimals as strings, ``None`` keys dropped.
    """
    return json.dumps(_normalize(value), separators=(",", ":"), ensure_ascii=False)


def sha256_canonical(value: Any) -> str:
    """sha256 hex digest of ``canonical_dumps(value)``."""
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()
