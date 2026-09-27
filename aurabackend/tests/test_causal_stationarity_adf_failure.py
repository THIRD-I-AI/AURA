"""BUG-213: an ADF test that cannot run must be reported, not silently treated as stationary."""
import numpy as np
import pandas as pd

from causal_service import discovery


def _noise(n=120, seed=0):
    return pd.Series(np.random.default_rng(seed).normal(size=n))


def test_adf_exception_is_reported_in_the_verdict(monkeypatch):
    monkeypatch.setattr(discovery, "_STATSMODELS_AVAILABLE", True)

    def boom(*a, **k):
        raise ValueError("Invalid input, x is constant")

    monkeypatch.setattr(discovery, "adfuller", boom, raising=False)
    v = discovery.check_stationarity(_noise())
    assert v.adf_p_value is None
    assert any("could not run" in r and "NOT confirmed" in r for r in v.reasons)
    # advisory, not blocking: "could not test" is not "non-stationary"
    assert v.stationary is True


def test_a_working_adf_adds_no_advisory(monkeypatch):
    monkeypatch.setattr(discovery, "_STATSMODELS_AVAILABLE", True)
    monkeypatch.setattr(discovery, "adfuller", lambda *a, **k: (0.0, 0.001), raising=False)
    v = discovery.check_stationarity(_noise())
    assert v.stationary is True and v.adf_p_value == 0.001
    assert not any("could not run" in r for r in v.reasons)


def test_a_real_nonstationary_finding_still_blocks(monkeypatch):
    monkeypatch.setattr(discovery, "_STATSMODELS_AVAILABLE", True)
    monkeypatch.setattr(discovery, "adfuller", lambda *a, **k: (0.0, 0.9), raising=False)
    v = discovery.check_stationarity(_noise())
    assert v.stationary is False


def test_advisory_reaches_the_response_warnings(monkeypatch):
    monkeypatch.setattr(discovery, "_DOWHY_AVAILABLE", True)
    monkeypatch.setattr(discovery, "_STATSMODELS_AVAILABLE", True)
    monkeypatch.setattr(discovery, "_gcm_attribute", lambda *a, **k: [])

    def boom(*a, **k):
        raise ValueError("numerical failure")

    monkeypatch.setattr(discovery, "adfuller", boom, raising=False)
    rng = np.random.default_rng(1)
    train = pd.DataFrame({"a": rng.normal(size=120), "y": rng.normal(size=120)})
    anom = pd.DataFrame({"a": [1.0, 2.0], "y": [3.0, 4.0]})
    _, method, warnings, verdict = discovery.attribute(
        train, anom, target="y", candidates=["a"], edges=None, method="gcm", top_k=3,
        enforce_stationarity=True,
    )
    assert method == "gcm"
    assert any("could not run" in w for w in warnings)
