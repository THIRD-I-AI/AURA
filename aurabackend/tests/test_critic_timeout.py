"""A hanging/rate-limited adversarial-critic LLM call must never block a numeric
audit. run_job(critic_timeout=...) bounds it; deterministic checks still apply."""
import asyncio
import os
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _small_query():
    from counterfactual_service.schemas import (
        CounterfactualQuery,
        DAGSpec,
        DatasetRef,
        InterventionSpec,
        OutcomeSpec,
    )
    return CounterfactualQuery(
        question="effect of t on y",
        treatment=InterventionSpec(column="t", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=DAGSpec(edges=[("x", "t"), ("x", "y"), ("t", "y")]),
        dataset=DatasetRef(source_id="inline"),
    )


def _small_df(n=200):
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, n)
    t = ((0.6 * x + rng.normal(0, 0.5, n)) > 0).astype(float)
    y = (0.5 * x - 0.6 * t + rng.normal(0, 0.3, n))
    return pd.DataFrame({"x": x, "t": t, "y": y})


def test_run_job_bounds_a_hanging_critic(monkeypatch):
    pytest.importorskip("dowhy")
    from counterfactual_service import engine

    async def _hang(*a, **k):
        await asyncio.sleep(60)  # simulate a rate-limited / stuck LLM critic

    monkeypatch.setattr(engine, "_run_critic", _hang)

    t0 = time.perf_counter()
    art = asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"],
                                     critic_timeout=1.0))
    dt = time.perf_counter() - t0

    # Did NOT wait the full 60s hang — the critic was bounded.
    assert dt < 45, f"audit blocked {dt:.0f}s on the hanging critic"
    # Deterministic fallback fired: critic not regenerated, and the skip is
    # surfaced in the signed artifact's warnings.
    assert art.regenerated_critic is False
    assert any("critic" in w.lower() and "skip" in w.lower() for w in art.warnings), art.warnings
    # The audit still produced a signed, hashed result.
    assert art.audit_record_hash
    assert any(e.method == "tmle" and e.error is None for e in art.estimates)


def test_run_job_without_timeout_runs_critic_normally(monkeypatch):
    """Default path (no critic_timeout) is unchanged — the demo relies on it."""
    pytest.importorskip("dowhy")
    from counterfactual_service import engine
    from counterfactual_service.schemas import AdversarialChallenge

    called = {"n": 0}

    async def _fake_critic(*a, **k):
        called["n"] += 1
        return [AdversarialChallenge(text="ok", severity="low")], True

    monkeypatch.setattr(engine, "_run_critic", _fake_critic)
    art = asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"]))
    assert called["n"] == 1
    assert art.regenerated_critic is True
    assert not any("critic" in w.lower() and "skip" in w.lower() for w in art.warnings)
