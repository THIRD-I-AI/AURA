"""DPC dual-paradigm SQL verification — Tier A unit tests (LLM/tools faked)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.dpc_verifier import VerificationResult, results_agree


def test_verification_result_defaults():
    vr = VerificationResult(status="skipped", verified=None, reason="multi-table")
    assert vr.status == "skipped"
    assert vr.verified is None
    assert vr.method == "dual_paradigm_pandas"
    assert vr.pandas_expr is None


def test_results_agree_scalar_match():
    # SQL returned one cell = 6; pandas computed scalar 6 → agree.
    assert results_agree(["s"], [[6]], 6) is True


def test_results_agree_scalar_mismatch():
    assert results_agree(["s"], [[5]], 6) is False


def test_results_agree_within_tolerance():
    assert results_agree(["s"], [[1.0000001]], 1.0, tol=1e-6) is True


def test_results_agree_order_insensitive():
    # Row order differs between SQL and pandas — still the same value set.
    pandas_side = pd.DataFrame({"g": ["b", "a"], "v": [3, 1]})
    assert results_agree(["g", "v"], [["a", 1], ["b", 3]], pandas_side) is True


def test_results_agree_series_and_none():
    s = pd.Series([10, 20])
    assert results_agree(["c"], [[10], [20]], s) is True
    assert results_agree(["c"], [[None]], pd.Series([np.nan])) is True
