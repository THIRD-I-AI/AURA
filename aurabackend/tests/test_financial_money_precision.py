"""Monetary sums must not drift — these figures get SIGNED.

The materiality basis and the AS-2305 per-account expectations were summed as
binary floats. Over a large ledger those additions accumulate representation
error, and the result is written into a signed audit event: two runs over the
same data could disagree in the last cents and both be attested as fact.

The tests below assert the ARITHMETIC, not the implementation. Asserting
"Decimal is used" would pass even if the code summed Decimals wrongly.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.specialists.financial_auditor import _money


def test_money_is_exact_where_float_is_not():
    """The canonical demonstration: 0.1 + 0.2 != 0.3 in binary floating point."""
    assert 0.1 + 0.2 != 0.3                      # the defect, in one line
    assert _money("0.1") + _money("0.2") == _money("0.3")


def test_money_converts_via_str_not_binary_float():
    """Decimal(0.1) inherits the float's error; Decimal("0.1") does not.
    Passing a float through _money must still land on the exact value."""
    assert _money(0.1) == Decimal("0.1")
    assert _money(0.1) != Decimal(0.1)           # the trap this avoids


def test_summing_many_cents_does_not_drift():
    """10,000 rows of $0.07 is exactly $700.00."""
    rows = [0.07] * 10_000
    exact = sum((_money(r) for r in rows), Decimal(0))
    assert exact == Decimal("700.00")
    assert float(exact) == 700.0


def test_unusable_rows_contribute_zero_rather_than_raising():
    """A malformed amount must not abort an audit mid-ledger — the
    surrounding checks already skip unusable rows."""
    for bad in (None, "", "n/a", object(), float("nan"), float("inf")):
        assert _money(bad) == Decimal(0)
