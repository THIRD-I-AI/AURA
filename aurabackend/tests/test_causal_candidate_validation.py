"""BUG-211: causal discover must validate candidates against the anomaly frame too."""
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from causal_service import discovery
from causal_service.main import causal_discover
from causal_service.models import CausalDiscoverRequest, DataSource


def _rows(n, cols, seed=0):
    rng = np.random.default_rng(seed)
    return [{c: float(rng.normal()) for c in cols} for _ in range(n)]


def _req(cands, anomaly_cols=("a", "y"), **kw):
    return CausalDiscoverRequest(
        target_metric="y",
        training_data=DataSource(rows=_rows(60, ["a", "b", "y"])),
        anomaly_data=DataSource(rows=_rows(5, list(anomaly_cols), seed=1)),
        candidate_causes=cands,
        method="correlation",
        enforce_stationarity=False,
        **kw,
    )


@pytest.mark.asyncio
async def test_candidate_missing_from_anomaly_data_is_a_400_not_a_500():
    with pytest.raises(HTTPException) as e:
        await causal_discover(_req(["a", "b"], anomaly_cols=("a", "y")))
    assert e.value.status_code == 400
    assert "anomaly data" in e.value.detail and "'b'" in e.value.detail


@pytest.mark.asyncio
async def test_target_and_duplicates_are_dropped_from_candidates():
    res = await causal_discover(_req(["a", "y", "a"], anomaly_cols=("a", "y")))
    assert [a.cause for a in res.attributions] == ["a"]


@pytest.mark.asyncio
async def test_only_the_target_as_candidate_is_rejected():
    with pytest.raises(HTTPException) as e:
        await causal_discover(_req(["y"]))
    assert e.value.status_code == 400


def test_gcm_returns_no_attributions_for_an_all_nan_anomaly_frame():
    train = pd.DataFrame(_rows(60, ["a", "y"]))
    anom = pd.DataFrame({"a": [np.nan, 1.0], "y": [1.0, np.nan]})
    warnings = []
    assert discovery._gcm_attribute(train, anom, "y", ["a"], None, warnings) == []
    assert any("No complete anomaly rows" in w for w in warnings)
