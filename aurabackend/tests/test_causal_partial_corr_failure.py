"""BUG-214: a failed partial correlation must be reported, not turned into a real-looking 0.0."""
import numpy as np
import pandas as pd

from causal_service import discovery


def _frames():
    rng = np.random.default_rng(0)
    a = rng.normal(size=80)
    train = pd.DataFrame({"a": a, "b": rng.normal(size=80), "y": 2 * a + rng.normal(size=80) * 0.1})
    anom = pd.DataFrame({"a": [3.0, 3.5], "b": [0.1, 0.2], "y": [6.0, 7.0]})
    return train, anom


def test_a_failing_candidate_is_flagged_and_the_others_still_score(monkeypatch):
    train, anom = _frames()
    real = discovery._partial_corr

    def flaky(df, x, y, controls):
        if x == "b":
            raise np.linalg.LinAlgError("singular matrix")
        return real(df, x, y, controls)

    monkeypatch.setattr(discovery, "_partial_corr", flaky)
    warnings = []
    out = {a.cause: a for a in discovery._correlation_attribute(train, anom, "y", ["a", "b"], warnings)}

    assert out["b"].score == 0.0 and out["b"].direction == "unknown" and out["b"].confidence == 0.0
    assert out["a"].score > 0
    assert len(warnings) == 1 and "'b'" in warnings[0] and "could not be computed" in warnings[0]


def test_no_warning_when_everything_computes():
    train, anom = _frames()
    warnings = []
    discovery._correlation_attribute(train, anom, "y", ["a", "b"], warnings)
    assert warnings == []


def test_warnings_argument_is_optional(monkeypatch):
    train, anom = _frames()
    monkeypatch.setattr(discovery, "_partial_corr", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    res = discovery._correlation_attribute(train, anom, "y", ["a", "b"])
    assert [r.score for r in res] == [0.0, 0.0]


def test_attribute_surfaces_the_warning_end_to_end(monkeypatch):
    train, anom = _frames()
    monkeypatch.setattr(discovery, "_partial_corr", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    _, method, warnings, _ = discovery.attribute(
        train, anom, target="y", candidates=["a", "b"], edges=None, method="correlation",
        top_k=5, enforce_stationarity=False,
    )
    assert method == "correlation"
    assert sum("could not be computed" in w for w in warnings) == 2
