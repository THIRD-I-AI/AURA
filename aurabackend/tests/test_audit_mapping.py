"""Audit-your-own-data — column-mapping helpers (Tier A, pure)."""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.audit_mapping import build_dag_from_mapping


def _mapping(instrument=None):
    return {"uploaded_file": "decisions.csv", "treatment": "t", "outcome": "y",
            "confounders": ["x"], "instrument": instrument}


# ── build_dag_from_mapping ──────────────────────────────────────────

def test_dag_backdoor_edges_no_instrument():
    dag = build_dag_from_mapping("flag", "approved", ["income", "dti"], None)
    edges = set(dag.edges)
    assert ("income", "flag") in edges and ("income", "approved") in edges
    assert ("dti", "flag") in edges and ("dti", "approved") in edges
    assert ("flag", "approved") in edges
    assert not any(e[1] == "flag" and e[0] not in ("income", "dti") for e in edges)


def test_dag_includes_instrument_edge():
    dag = build_dag_from_mapping("flag", "approved", ["income"], "officer")
    assert ("officer", "flag") in set(dag.edges)
    assert ("officer", "approved") not in set(dag.edges)  # exclusion restriction


def test_dag_rejects_self_loop_when_confounder_equals_treatment():
    with pytest.raises(Exception):
        build_dag_from_mapping("flag", "approved", ["flag"], None)
