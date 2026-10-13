"""BUG-421: challenges are ordered most severe first, not alphabetically by severity."""
from __future__ import annotations

import json

import pytest

from counterfactual_service.engine import run_job
from counterfactual_service.schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)
from tests._mock_llm import UnifiedMockLLM, install_mock
from tests._synthetic_data import synthetic_dag_full, synthetic_dataset


@pytest.mark.asyncio
async def test_medium_challenges_rank_ahead_of_low_ones(monkeypatch, tmp_path):
    critic = {"challenges": [
        {"text": "a minor caveat", "severity": "low"},
        {"text": "another minor caveat", "severity": "low"},
        {"text": "a material concern", "severity": "medium"},
    ]}
    install_mock(monkeypatch, UnifiedMockLLM(default_response=json.dumps(critic)))
    monkeypatch.setenv("AURA_AUDIT_DIR", str(tmp_path))
    query = CounterfactualQuery(
        question="order",
        treatment=InterventionSpec(column="treatment", actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column="outcome", agg="sum", window=("2025-01-01", "2025-12-31")),
        dag=DAGSpec(edges=synthetic_dag_full()["edges"]),
        dataset=DatasetRef(source_id="order"),
    )

    artifact = await run_job(query, df=synthetic_dataset(n=500))

    rank = {"high": 0, "medium": 1, "low": 2}
    ranks = [rank[c.severity] for c in artifact.challenges]
    assert {"low", "medium"} <= {c.severity for c in artifact.challenges}
    assert ranks == sorted(ranks), [c.severity for c in artifact.challenges]
