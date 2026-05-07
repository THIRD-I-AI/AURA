"""
aura-counterfactual — Python SDK for the AURA Counterfactual Audit Engine.

Quickstart::

    from aura_counterfactual import Client

    with Client(base_url="http://localhost:8000") as c:
        info = c.info()
        print(info.engine_version, info.dowhy_available)

        artifact = c.run({
            "question":  "What if X had been different?",
            "treatment": {"column": "x", "actual": 1, "counterfactual": 0},
            "outcome":   {"column": "y", "agg": "sum",
                          "window": ["2025-01-01", "2025-12-31"]},
            "dag":       {"edges": [["x", "y"]]},
            "dataset":   {"source_id": "uploaded_file:my.csv"},
            "audience":  "analyst",
        })

        # Replay later (byte-identical)
        artifact_again = c.replay(artifact.audit_record_hash)

        # Verify the signature without needing the private key
        result = c.verify(artifact.audit_record_hash)
        assert result.verified, result.reason

In a Jupyter notebook the artifact prints rich HTML.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .client import (
    AsyncClient,
    Client,
    EngineError,
    JobFailedError,
    JobTimeoutError,
    NotFoundError,
    RetryPolicy,
    ServiceUnavailableError,
)
from .models import (
    AdversarialChallenge,
    Audience,
    CounterfactualArtifact,
    CounterfactualEstimate,
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    EngineInfo,
    EstimatorMethod,
    InterventionSpec,
    JobStatus,
    OutcomeSpec,
    RefutationResult,
    RefuterName,
    Severity,
    VerifyResult,
)


def replay(record_hash: str, *, base_url: str = "http://localhost:8000") -> CounterfactualArtifact:
    """One-shot convenience: ``aura_counterfactual.replay(hash)``.

    Equivalent to ``Client(base_url).replay(hash)`` with default settings.
    Useful for quick notebook drill-downs:

        >>> import aura_counterfactual as ac
        >>> art = ac.replay("0xabc...")
        >>> art   # renders rich HTML in Jupyter
    """
    with Client(base_url=base_url) as c:
        return c.replay(record_hash)


__all__ = [
    "__version__",
    # Clients
    "Client",
    "AsyncClient",
    "RetryPolicy",
    # Errors
    "EngineError",
    "JobFailedError",
    "JobTimeoutError",
    "NotFoundError",
    "ServiceUnavailableError",
    # Models
    "AdversarialChallenge",
    "Audience",
    "CounterfactualArtifact",
    "CounterfactualEstimate",
    "CounterfactualQuery",
    "DAGSpec",
    "DatasetRef",
    "EngineInfo",
    "EstimatorMethod",
    "InterventionSpec",
    "JobStatus",
    "OutcomeSpec",
    "RefutationResult",
    "RefuterName",
    "Severity",
    "VerifyResult",
    # Top-level conveniences
    "replay",
]
