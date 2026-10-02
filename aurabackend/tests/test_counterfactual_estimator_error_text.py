"""BUG-292: a failed estimate's ``error`` was the raw ``str(exc)`` of whatever econml,
dowhy or numpy raised -- returned by the job endpoints and sealed into the signed
artifact, bypassing sanitize_error."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from counterfactual_service import engine
from counterfactual_service.schemas import InterventionSpec, OutcomeSpec


@pytest.mark.parametrize("path", [
    r"C:\Users\svc\aura\data\uploads\acme\loans.csv",
    "/opt/aura/aurabackend/data/uploads/acme/loans.csv",
])
def test_server_paths_are_removed(path):
    text = engine._estimator_error(OSError(f"cannot read {path}: permission denied"))

    assert "aura" not in text and "<path>" in text
    assert text.startswith("OSError: ") and "permission denied" in text


def test_a_numeric_failure_stays_readable():
    assert engine._estimator_error(np.linalg.LinAlgError("Singular matrix")) == "LinAlgError: Singular matrix"


def test_only_the_first_line_is_kept_and_it_is_capped():
    text = engine._estimator_error(RuntimeError("short reason\nTraceback (most recent call last):\n  secret frame"))
    assert text == "RuntimeError: short reason"

    assert len(engine._estimator_error(ValueError("x" * 5000))) <= len("ValueError: ") + 200


def test_a_prefix_and_an_empty_message():
    assert engine._estimator_error(ValueError("no instrument in DAG"), "IV (2SLS) failed") \
        == "IV (2SLS) failed: ValueError: no instrument in DAG"
    assert engine._estimator_error(KeyError()) == "KeyError"


def test_a_failing_estimator_reports_through_the_helper(monkeypatch):
    """The DoWhy path: whatever the library raises reaches the artifact scrubbed."""
    def _boom(*a, **k):
        raise RuntimeError("could not load /srv/aura/models/cache.pkl\ninternal detail")

    monkeypatch.setattr(engine, "_build_causal_model", _boom)
    df = pd.DataFrame({"t": [0.0, 1.0] * 20, "y": [1.0, 2.0] * 20, "x": [0.5] * 40})

    est = engine._run_one_estimator(
        "linear_regression", df, InterventionSpec(column="t", actual=1, counterfactual=0),
        OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        {"edges": [("x", "t"), ("x", "y"), ("t", "y")]}, seed=0,
    )

    assert est.error == "RuntimeError: could not load <path>"
