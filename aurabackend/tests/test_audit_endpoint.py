"""Audit-your-own-data — worker + endpoint."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_demo_like_csv(path):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(7)
    n = 600
    x = rng.normal(0, 1, n)
    t = ((0.6 * x + rng.normal(0, 0.5, n)) > 0).astype(int)
    y = (0.5 + 0.4 * x - 0.6 * t + rng.normal(0, 0.3, n))
    pd.DataFrame({"flag": t, "approved": y, "score": x}).to_csv(path, index=False)


# ── Tier B: out-of-process worker produces a signed, honest artifact ─

def test_run_audit_subprocess_produces_signed_artifact_with_honesty(tmp_path, monkeypatch):
    pytest.importorskip("econml")
    monkeypatch.chdir(tmp_path)
    up = tmp_path / "data" / "uploads"
    up.mkdir(parents=True)
    _write_demo_like_csv(up / "decisions.csv")

    from counterfactual_service.audit_worker import run_audit_subprocess
    result = run_audit_subprocess({
        "uploaded_file": "decisions.csv", "treatment": "flag",
        "outcome": "approved", "confounders": ["score"], "instrument": None,
    })
    methods = {e["method"] for e in result["estimates"]}
    assert {"double_ml", "tmle"}.issubset(methods)
    assert result["audit_record_hash"]
    assert result["signature_status"] == "signed"
    assert "identification" in result and "no unmeasured confounding" in result["identification"]
    assert "sensitivity_headline" in result
    assert result["data_quality"]["n_clean"] >= 100
