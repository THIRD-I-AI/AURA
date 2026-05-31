"""Turn a user column-mapping into a validated DataFrame + a causally-honest
CounterfactualQuery. Pure functions — no I/O, no engine calls — so they're
trivially testable and safe to run in a child process."""
from __future__ import annotations

from typing import List, Optional, Tuple

import pandas as pd
from pydantic import BaseModel

from .schemas import (
    CounterfactualQuery,
    DAGSpec,
    DatasetRef,
    InterventionSpec,
    OutcomeSpec,
)

AUDIT_MIN_ROWS = 100


def build_dag_from_mapping(
    treatment: str,
    outcome: str,
    confounders: List[str],
    instrument: Optional[str],
) -> DAGSpec:
    """Canonical backdoor DAG: confounders point at both treatment and outcome,
    treatment points at outcome, and an instrument (if any) points at treatment
    only (the exclusion restriction). DAGSpec rejects self-loops, so a confounder
    equal to the treatment/outcome raises."""
    edges: List[Tuple[str, str]] = []
    for c in confounders:
        edges.append((c, treatment))
        edges.append((c, outcome))
    edges.append((treatment, outcome))
    if instrument:
        edges.append((instrument, treatment))
    return DAGSpec(edges=edges)
