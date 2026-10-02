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


# ── BUG-294: a FAILED critic run is not "the critic had no objections" ─

class _FailingCritic:
    """Stands in for AdversarialCriticAgent: BaseAgent.execute reports an LLM error,
    non-JSON output or an exhausted budget as a FAILED result, without raising."""
    calls = 0

    class llm:
        model = "m"
        model_version = "v1"

    async def execute(self, ctx):
        from agents.base import AgentResult, AgentStatus

        type(self).calls += 1
        return AgentResult(status=AgentStatus.FAILED, error="provider returned 429")


def _use_failing_critic(monkeypatch, tmp_path):
    import agents.specialists.adversarial_critic_agent as critic_module
    from counterfactual_service import critic_cache

    stored = {}
    monkeypatch.setattr(critic_cache, "get", lambda k: stored.get(k))
    monkeypatch.setattr(critic_cache, "put", lambda k, v: stored.__setitem__(k, v))
    _FailingCritic.calls = 0
    monkeypatch.setattr(critic_module, "AdversarialCriticAgent", _FailingCritic)
    return stored


@pytest.mark.parametrize("critic_timeout", [None, 30.0])
def test_a_failed_critic_is_surfaced_and_never_cached(monkeypatch, tmp_path, critic_timeout):
    pytest.importorskip("dowhy")
    from counterfactual_service import engine

    stored = _use_failing_critic(monkeypatch, tmp_path)

    art = asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"],
                                     critic_timeout=critic_timeout))

    assert any(w.startswith(engine.CRITIC_SKIPPED_PREFIX) for w in art.warnings), art.warnings
    assert art.regenerated_critic is False
    assert stored == {}, "a failed run must not be written to the critic cache"

    # The provider recovers: the next run must reach the critic again, not replay [].
    asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"],
                               critic_timeout=critic_timeout))
    assert _FailingCritic.calls == 2


def test_the_operator_card_and_pdf_do_not_claim_no_objections(monkeypatch, tmp_path):
    pytest.importorskip("dowhy")
    from counterfactual_service import engine
    from counterfactual_service.renderers import render

    _use_failing_critic(monkeypatch, tmp_path)
    art = asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"]))

    assert render(art, "operator").get("critic_skipped") is True


def test_a_critic_that_ran_and_found_nothing_is_not_marked_skipped(monkeypatch):
    pytest.importorskip("dowhy")
    from counterfactual_service import engine
    from counterfactual_service.renderers import render

    async def _quiet_critic(*a, **k):
        return [], True

    monkeypatch.setattr(engine, "_run_critic", _quiet_critic)
    art = asyncio.run(engine.run_job(_small_query(), df=_small_df(), methods=["tmle"]))

    assert not any(w.startswith(engine.CRITIC_SKIPPED_PREFIX) for w in art.warnings)
    assert "critic_skipped" not in render(art, "operator")
