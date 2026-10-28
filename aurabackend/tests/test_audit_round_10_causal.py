"""Audit round 10, causal area: BUG-523 .. BUG-527."""
import time

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from causal_service import discovery
from causal_service.main import causal_discover
from causal_service.models import CausalDiscoverRequest, DataSource

# Tier B: listed in the causal-test CI lane, the only one that installs dowhy (BUG-303).
needs_dowhy = pytest.mark.skipif(not discovery.dowhy_available(), reason="dowhy not installed")


# The joint gcm budget BUG-523 introduced (rows x Shapley set evaluations), pinned here so a
# regression to "unbounded" fails loudly. With the default graph, k candidates are k + 1 players.
BUDGET = 100


def _training(n=500, seed=0, k=2, nonlinear=False):
    """a, b (, c...) ~ N(0,1); y = sum of causes + small noise. True effect of each cause on y is +1.
    ``nonlinear`` uses y = sum(sin(3x) + x^2), gcm's slowest mechanism fit (BUG-523 timing)."""
    rng = np.random.default_rng(seed)
    cols = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"][:k]
    df = pd.DataFrame({c: rng.normal(size=n) for c in cols})
    signal = (np.sin(3 * df[cols]) + df[cols] ** 2).sum(axis=1) if nonlinear else df[cols].sum(axis=1)
    df["y"] = signal + rng.normal(scale=0.3, size=n)
    return df


def _req(train, anomaly_rows, **kw):
    kw.setdefault("method", "gcm")
    kw.setdefault("enforce_stationarity", False)
    return CausalDiscoverRequest(
        target_metric="y",
        training_data=DataSource(rows=train.to_dict("records")),
        anomaly_data=DataSource(rows=anomaly_rows),
        **kw,
    )


# ── BUG-524 / BUG-525: one anomaly row, a drives it, b mitigates it ──

@pytest.fixture(scope="module")
def single_row_result():
    np.random.seed(0)  # gcm samples noise through the global numpy RNG
    train = _training()
    anom = pd.DataFrame([{"a": 3.5, "b": -0.6, "y": 2.9}])
    attrs, _, warnings, _ = discovery.attribute(
        train, anom, target="y", candidates=["a", "b"], edges=None,
        method="gcm", top_k=5, enforce_stationarity=False,
    )
    return {a.cause: a for a in attrs}, [a.cause for a in attrs], warnings


@needs_dowhy
def test_bug_524_mitigating_cause_has_negative_score_and_ranks_last(single_row_result):
    by_cause, order, _ = single_row_result
    # b = -0.6 pulled y = a + b DOWN, so its Shapley contribution to the anomaly is negative
    # (raw gcm ~ -0.4). The old engine reported abs(mean) = +0.24, a "driver".
    assert by_cause["b"].score < -0.1
    # a = +3.5 sigma is the real driver (raw gcm ~ +3.7).
    assert by_cause["a"].score == pytest.approx(3.7, abs=1.0)
    assert order == ["a", "b"]
    text = discovery.summarise([by_cause[c] for c in order], "y", "gcm")
    assert "Secondary driver" not in text


def test_bug_524_summary_does_not_name_a_mitigator_as_driver():
    from causal_service.models import Attribution
    attrs = [Attribution(cause="a", score=2.0), Attribution(cause="b", score=-0.4)]
    text = discovery.summarise(attrs, "y", "gcm")
    assert "**a**" in text and "Secondary driver" not in text
    text = discovery.summarise([Attribution(cause="b", score=-0.4)], "y", "gcm")
    assert "Top driver" not in text


@needs_dowhy
def test_bug_525_single_row_confidence_is_not_reported_as_certain(single_row_result):
    by_cause, _, warnings = single_row_result
    # One sample has no spread to measure; the old code returned confidence=1.0 for everything.
    assert all(a.confidence == 0.0 for a in by_cause.values())
    assert any("single anomaly row" in w for w in warnings)


# ── BUG-523: compute is bounded ──

def test_bug_523_budget_matches_dowhy_default_shapley_counts():
    assert discovery._GCM_MAX_ROW_EVALUATIONS == BUDGET
    # exact Shapley up to 5 players, then 25 deduplicated permutations
    assert [discovery._gcm_set_evaluations(p) for p in (3, 5, 6, 8, 9)] == [8, 32, 64, 177, 202]


def _spy_attribute_anomalies(monkeypatch, real=False):
    seen = []
    original = discovery.gcm.attribute_anomalies

    def spy(scm, target_node, anomaly_samples, **kw):
        seen.append(list(anomaly_samples.index))
        if real:
            return original(scm, target_node=target_node, anomaly_samples=anomaly_samples, **kw)
        return {c: np.ones(len(anomaly_samples)) for c in anomaly_samples.columns}

    monkeypatch.setattr(discovery.gcm, "attribute_anomalies", spy)
    return seen


@needs_dowhy
@pytest.mark.asyncio
async def test_bug_523_rows_are_cut_to_the_budget_keeping_the_most_anomalous(monkeypatch):
    seen = _spy_attribute_anomalies(monkeypatch)
    train = _training()
    anomalies = _training(n=40, seed=7)
    anomalies.loc[3, ["a", "y"]] = [6.0, 6.0]
    res = await causal_discover(_req(train, anomalies.to_dict("records"), candidate_causes=["a", "b"]))
    # 2 candidates = 3 players = 8 exact set evaluations per row -> 100 // 8 = 12 rows
    assert len(seen) == 1 and len(seen[0]) == BUDGET // 8
    assert 3 in seen[0]
    assert any(f"{BUDGET // 8} most anomalous of 40" in w for w in res.warnings)


@needs_dowhy
@pytest.mark.slow
@pytest.mark.asyncio
async def test_bug_523_worst_case_at_the_budget_boundary_finishes_well_inside_the_gateway_timeout(monkeypatch):
    # 4 candidates = 5 players = 32 exact set evaluations per row -> 3 rows (96 of 100): the most
    # expensive request the budget allows, on its slowest input -- nonlinear mechanisms and the
    # default 10k-row training read (sampled to the training cap). Measured ~28 s locally; the
    # gateway proxy gives up at 60 s.
    seen = _spy_attribute_anomalies(monkeypatch, real=True)
    train = _training(n=10_000, k=4, nonlinear=True)
    anomalies = _training(n=20, seed=7, k=4, nonlinear=True)
    anomalies["y"] += 8.0
    t0 = time.perf_counter()
    res = await causal_discover(_req(train, anomalies.to_dict("records")))
    elapsed = time.perf_counter() - t0
    assert [len(rows) for rows in seen] == [BUDGET // 32]
    assert res.method_used == "gcm"
    assert elapsed < 40, f"gcm worst allowed case took {elapsed:.1f}s"
    assert any("sample of" in w for w in res.warnings)


@needs_dowhy
@pytest.mark.asyncio
async def test_bug_523_explicit_gcm_over_budget_is_a_400_before_any_fitting(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("gcm ran over its compute budget")

    monkeypatch.setattr(discovery.gcm.auto, "assign_causal_mechanisms", boom)
    monkeypatch.setattr(discovery.gcm, "attribute_anomalies", boom)
    train = _training(n=60, k=8)  # 9 players -> 202 set evaluations for even one row
    with pytest.raises(HTTPException) as e:
        await causal_discover(_req(train, train.head(1).to_dict("records")))
    assert e.value.status_code == 400
    assert "budget" in e.value.detail


@needs_dowhy
@pytest.mark.asyncio
async def test_bug_523_auto_on_a_wide_table_falls_back_to_correlation(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("gcm ran over its compute budget")

    monkeypatch.setattr(discovery.gcm, "attribute_anomalies", boom)
    train = _training(n=200, k=12)
    anomaly = train.head(2).copy()
    anomaly.loc[:, "a"] += 5.0
    anomaly.loc[:, "y"] += 5.0
    # method='auto' and no candidate_causes: every numeric column is a candidate (12 of them)
    res = await causal_discover(_req(train, anomaly.to_dict("records"), method="auto"))
    assert res.method_used == "correlation"
    assert res.attributions
    assert any("Falling back to the correlation engine" in w for w in res.warnings)


# ── BUG-526: user-supplied causal graph is validated ──

@needs_dowhy
@pytest.mark.asyncio
@pytest.mark.parametrize("edges, needle", [
    ([["a", "y"], ["b", "y"], ["y", "a"]], "cycle"),
    ([["a", "y"], ["b", "y"], ["zzz", "y"]], "zzz"),
])
async def test_bug_526_invalid_causal_graph_is_a_400(edges, needle):
    train = _training(n=60)
    with pytest.raises(HTTPException) as e:
        await causal_discover(_req(train, [{"a": 3.0, "b": 0.0, "y": 3.0}],
                                   candidate_causes=["a", "b"], causal_graph_edges=edges))
    assert e.value.status_code == 400
    assert needle in e.value.detail


@needs_dowhy
@pytest.mark.asyncio
async def test_bug_526_candidate_disconnected_from_target_is_flagged():
    train = _training(n=60)
    res = await causal_discover(_req(train, [{"a": 3.0, "b": 0.0, "y": 3.0}],
                                     candidate_causes=["a", "b"], causal_graph_edges=[["a", "y"]]))
    assert any("'b'" in w and "no directed path" in w for w in res.warnings)


# ── BUG-527: string-encoded numbers in anomaly rows ──

@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["correlation", pytest.param("gcm", marks=needs_dowhy)])
async def test_bug_527_string_numbers_in_anomaly_rows_are_coerced(method):
    train = _training(n=200)
    rows = [{"a": "5", "b": 0.1, "y": 5.0}, {"a": "7", "b": -0.1, "y": 7.0}]
    res = await causal_discover(_req(train, rows, candidate_causes=["a", "b"], method=method))
    assert res.attributions[0].cause == "a"


@pytest.mark.asyncio
async def test_bug_527_unparseable_anomaly_value_is_reported_not_a_500():
    train = _training(n=200)
    rows = [{"a": "n/a", "b": 0.1, "y": 5.0}, {"a": "7", "b": -0.1, "y": 7.0}]
    res = await causal_discover(_req(train, rows, candidate_causes=["a", "b"], method="correlation"))
    assert any("not numeric" in w and "'a'" in w for w in res.warnings)
