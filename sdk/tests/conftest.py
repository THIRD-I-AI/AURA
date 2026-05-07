"""Shared fixtures for the SDK test suite."""
from __future__ import annotations

from typing import Any, Dict

import pytest

SAMPLE_ARTIFACT: Dict[str, Any] = {
    "record_id": "ca_test_1",
    "query": {
        "question": "Test counterfactual?",
        "treatment": {"column": "t", "actual": 1.0, "counterfactual": 0.0},
        "outcome": {"column": "y", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        "dag": {"edges": [["t", "y"]]},
        "dataset": {"source_id": "ds"},
        "audience": "analyst",
    },
    "estimates": [
        {"method": "linear_regression", "point": 1.5, "ci_lower": 1.0, "ci_upper": 2.0,
         "n_samples": 100, "elapsed_ms": 12.3, "error": None},
        {"method": "psm", "point": 0, "ci_lower": 0, "ci_upper": 0,
         "n_samples": 100, "elapsed_ms": 11.0, "error": "binary required"},
    ],
    "refutations": [
        {"refuter": "placebo", "estimate_after": 0.02, "p_value": 0.04,
         "passed": True, "elapsed_ms": 5.5, "error": None},
        {"refuter": "random_common_cause", "estimate_after": 1.51,
         "p_value": 0.5, "passed": True, "elapsed_ms": 7.0, "error": None},
    ],
    "challenges": [
        {"text": "n_samples is small", "severity": "low",
         "suggested_check": "collect more data"},
        {"text": "DAG omits a known confounder", "severity": "high",
         "suggested_check": "add seasonality"},
    ],
    "confidence": "medium",
    "schema_version": "v1",
    "dataset_fingerprint": "f" * 64,
    "audit_record_hash": "a" * 64,
    "regenerated_critic": False,
    "signature_b64": "AAAA",
    "signature_status": "signed",
    "signing_key_source": "env_hex",
    "rendered": {},
    "warnings": [],
    "created_at": None,
}


@pytest.fixture
def sample_artifact() -> Dict[str, Any]:
    return SAMPLE_ARTIFACT
