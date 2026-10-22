"""BUG-506: string widths set by a literal, and the parent's unbounded read of the
child's reply, both bypassed the BUG-479 memory bounds of the DPC sandbox."""
from __future__ import annotations

import sys

import pandas as pd
import pytest

from agents import dpc_verifier
from agents.dpc_verifier import safe_eval_pandas


@pytest.mark.parametrize("expr", [
    'df["a"].astype("str").str.pad(100000000)',
    'df["a"].astype("str").str.zfill(100000000)',
    'df["a"].astype("str").str.center(100000000)',
    'df["a"].astype("str").str.ljust(100000000)',
    'df["a"].astype("str").str.rjust(100000000)',
    '"{:>100000000}".format(1)',
    'f"{1:>100000000}"',
    '"%100000000d" % 1',
    '"\t".expandtabs(100000000)',
])
def test_width_sized_strings_are_refused(expr):
    with pytest.raises(ValueError):
        safe_eval_pandas(expr, pd.DataFrame({"a": [1, 2]}))


@pytest.mark.parametrize("expr, expected", [
    ('df["a"].sum() % 2', 1),
    ('df["a"].astype("str").str.len().max()', 1),
])
def test_ordinary_string_and_modulo_expressions_still_evaluate(expr, expected):
    assert safe_eval_pandas(expr, pd.DataFrame({"a": [1, 2]})) == expected


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the capped child process is Linux-only")
def test_an_oversized_child_reply_is_refused_without_unpickling_it():
    with pytest.raises(MemoryError):
        dpc_verifier._run_in_capped_child(lambda: "x" * (dpc_verifier._MAX_RESULT_BYTES + 1024), 20)
    assert dpc_verifier._run_in_capped_child(lambda: 41 + 1, 20) == 42
