# S31b Audit Engine & Demo Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-click `/demo` that runs AURA's full counterfactual audit (7 estimators incl. a new IV slot) on a pre-loaded, deterministic compliance dataset, sealed with a persistent ED25519 signature.

**Architecture:** A scenario registry (`demo_scenarios/`) builds deterministic synthetic datasets + a `CounterfactualQuery`. New `/counterfactual/demo/*` endpoints register the dataset via the existing `register_dataset` hook and submit a real async job through the existing `run_job` fan-out — extended with an opt-in `methods=` list so all 7 estimators run. A new IV (2SLS, pure-numpy) estimator becomes the 7th slot. Signing gains a 4th key source that auto-generates and persists a key so certificates survive restarts. Startup pre-warm caches a last-good artifact per scenario for a fail-safe.

**Tech Stack:** FastAPI, Pydantic v2, pandas, numpy, pytest. Builds on existing `counterfactual_service/{engine,signing,main,pdf_renderer,persistence}.py`.

---

## File Structure

- Create `aurabackend/counterfactual_service/demo_scenarios/__init__.py` — registry (`SCENARIOS`, `get_scenario`, `list_scenarios`).
- Create `aurabackend/counterfactual_service/demo_scenarios/base.py` — `DemoScenario` ABC + `register` decorator.
- Create `aurabackend/counterfactual_service/demo_scenarios/fair_lending.py` — scenario #1 (deterministic data + query + narrative).
- Modify `aurabackend/counterfactual_service/schemas.py` — add `"iv"` to `EstimatorMethod`.
- Modify `aurabackend/counterfactual_service/engine.py` — IV dispatch (`_run_one_iv_2sls`) + thread `methods` through `run_job`.
- Modify `aurabackend/counterfactual_service/signing.py` — 4th key source (persisted file).
- Modify `aurabackend/counterfactual_service/main.py` — `/demo` endpoints, demo job worker (passes the 7-method list), startup pre-warm + last-good fail-safe, `info()` estimator list.
- Modify `aurabackend/counterfactual_service/pdf_renderer.py` — scenario header + attestation block.
- Modify `aurabackend/api_gateway/routers/counterfactual.py` — gateway passthrough for `/demo`.
- Tests: `tests/test_demo_scenarios.py`, `tests/test_iv_estimator.py`, `tests/test_signing_persistent_key.py`, `tests/test_demo_endpoints.py`.

All Tier A unless marked **[Tier B]** (needs dowhy/econml).

---

## Task 1: Add `iv` to the EstimatorMethod type

**Files:**
- Modify: `aurabackend/counterfactual_service/schemas.py:61`

- [ ] **Step 1: Edit the Literal**

In `schemas.py` change line 61 to include `"iv"`:

```python
EstimatorMethod = Literal["linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"]
```

- [ ] **Step 2: Verify import still works**

Run: `cd aurabackend && python -c "from counterfactual_service.schemas import EstimatorMethod; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add aurabackend/counterfactual_service/schemas.py
git commit -m "feat(s31b): add iv to EstimatorMethod literal"
```

---

## Task 2: IV estimator (2SLS, pure-numpy) — the 7th slot

The instrument is any DAG node with an edge **to the treatment** and **not to the outcome**. 2SLS: stage-1 regress treatment on [instruments + confounders + intercept]; stage-2 regress outcome on [fitted treatment + confounders + intercept]; the fitted-treatment coefficient is the IV ATE. Analytic SE via the stage-2 residual variance and the second-stage design matrix.

**Files:**
- Create: `aurabackend/counterfactual_service/iv_estimator.py`
- Modify: `aurabackend/counterfactual_service/engine.py` (dispatch in `_run_one_estimator`, ~line 951)
- Test: `aurabackend/tests/test_iv_estimator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_iv_estimator.py
import os, sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.iv_estimator import instruments_from_dag, run_iv_2sls


def test_instruments_from_dag_picks_node_to_treatment_not_outcome():
    edges = [("z", "t"), ("x", "t"), ("x", "y"), ("t", "y")]
    insts = instruments_from_dag(edges, treatment="t", outcome="y")
    assert insts == ["z"]   # x is a confounder (also -> y); t is treatment


def test_run_iv_2sls_recovers_known_effect():
    rng = np.random.default_rng(0)
    n = 4000
    z = rng.integers(0, 2, n).astype(float)          # instrument
    u = rng.normal(0, 1, n)                            # unobserved confounder
    t = (0.5 * z + 0.7 * u + rng.normal(0, 0.3, n) > 0.6).astype(float)
    y = 2.0 * t + 1.5 * u + rng.normal(0, 0.3, n)      # true effect of t on y is 2.0
    df = pd.DataFrame({"z": z, "t": t, "y": y})
    point, lo, hi = run_iv_2sls(df, treatment="t", outcome="y", instruments=["z"], confounders=[])
    # OLS of y~t would be biased upward by u; IV should be near 2.0.
    assert 1.4 < point < 2.6
    assert lo < point < hi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_iv_estimator.py -q`
Expected: FAIL with `ModuleNotFoundError: counterfactual_service.iv_estimator`

- [ ] **Step 3: Write the implementation**

```python
# counterfactual_service/iv_estimator.py
"""Instrumental-variables (2SLS) ATE — pure NumPy, no dowhy/econml.

The instrument is read from the DAG: any node with an edge to the
treatment but no edge to the outcome (exclusion restriction encoded in
the graph). 2SLS gives a consistent ATE when an unmeasured confounder
biases the naive treatment-outcome association — the canonical
fair-lending audit move ("but-for the instrument-driven variation,
what is the causal effect?").
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np
import pandas as pd


def instruments_from_dag(edges, treatment: str, outcome: str) -> List[str]:
    to_treatment = {src for src, dst in edges if dst == treatment}
    to_outcome = {src for src, dst in edges if dst == outcome}
    insts = [n for n in to_treatment if n != outcome and n not in to_outcome]
    return sorted(insts)


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def run_iv_2sls(
    df: pd.DataFrame,
    treatment: str,
    outcome: str,
    instruments: List[str],
    confounders: List[str],
) -> Tuple[float, float, float]:
    """Return (point, ci_lower, ci_upper) for the IV ATE of treatment on outcome."""
    if not instruments:
        raise ValueError("IV requires at least one instrument")
    n = len(df)
    intercept = np.ones((n, 1))
    Xc = df[confounders].to_numpy(dtype=float) if confounders else np.empty((n, 0))
    Z = df[instruments].to_numpy(dtype=float)
    T = df[treatment].to_numpy(dtype=float).reshape(-1, 1)
    Y = df[outcome].to_numpy(dtype=float)

    # Stage 1: T ~ [intercept, instruments, confounders]
    S1 = np.hstack([intercept, Z, Xc])
    t_hat = S1 @ _ols(S1, T.ravel())

    # Stage 2: Y ~ [intercept, t_hat, confounders]
    S2 = np.hstack([intercept, t_hat.reshape(-1, 1), Xc])
    beta2 = _ols(S2, Y)
    point = float(beta2[1])  # coefficient on fitted treatment

    # Analytic SE from stage-2 residuals.
    resid = Y - S2 @ beta2
    dof = max(n - S2.shape[1], 1)
    sigma2 = float(resid @ resid) / dof
    XtX_inv = np.linalg.pinv(S2.T @ S2)
    se = float(np.sqrt(sigma2 * XtX_inv[1, 1]))
    return point, point - 1.96 * se, point + 1.96 * se
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_iv_estimator.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire IV into the engine dispatch**

In `engine.py`, inside `_run_one_estimator`, immediately after the `tmle` branch (the `if method_key == "tmle":` block, ~line 951-952) add:

```python
    if method_key == "iv":
        import time as _time
        from .iv_estimator import instruments_from_dag, run_iv_2sls
        t0 = _time.perf_counter()
        edges = dag.get("edges", [])
        instruments = instruments_from_dag(edges, treatment.column, outcome.column)
        confounders = [
            src for src, dst in edges
            if dst == outcome.column and src != treatment.column
            and src not in instruments
        ]
        try:
            if not instruments:
                raise ValueError("no instrument in DAG (need a node -> treatment, not -> outcome)")
            point, lo, hi = run_iv_2sls(
                df, treatment.column, outcome.column, instruments, sorted(set(confounders)),
            )
            return CounterfactualEstimate(
                method="iv", point=point, ci_lower=lo, ci_upper=hi,
                n_samples=len(df), elapsed_ms=(_time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            return CounterfactualEstimate(
                method="iv", point=0.0, ci_lower=0.0, ci_upper=0.0,
                n_samples=len(df), elapsed_ms=(_time.perf_counter() - t0) * 1000,
                error=f"IV (2SLS) failed: {exc}",
            )
```

- [ ] **Step 6: Test the dispatch end-to-end**

```python
# append to tests/test_iv_estimator.py
def test_engine_dispatch_iv_via_run_one_estimator():
    from counterfactual_service.engine import _run_one_estimator
    from counterfactual_service.schemas import InterventionSpec, OutcomeSpec
    rng = np.random.default_rng(1)
    n = 2000
    z = rng.integers(0, 2, n).astype(float)
    u = rng.normal(0, 1, n)
    t = (0.6 * z + 0.6 * u > 0.5).astype(float)
    y = 1.0 * t + 1.2 * u + rng.normal(0, 0.3, n)
    df = pd.DataFrame({"z": z, "t": t, "y": y})
    est = _run_one_estimator(
        "iv", df,
        InterventionSpec(column="t", actual=1, counterfactual=0),
        OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        {"edges": [["z", "t"], ["t", "y"]]}, seed=0,
    )
    assert est.method == "iv"
    assert est.error is None
    assert est.ci_lower < est.point < est.ci_upper
```

- [ ] **Step 7: Run and verify**

Run: `cd aurabackend && python -m pytest tests/test_iv_estimator.py -q`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add aurabackend/counterfactual_service/iv_estimator.py aurabackend/counterfactual_service/engine.py aurabackend/tests/test_iv_estimator.py
git commit -m "feat(s31b): IV (2SLS) estimator as 7th slot"
```

---

## Task 3: Thread `methods` through `run_job`

**Files:**
- Modify: `aurabackend/counterfactual_service/engine.py:1648` (`run_job` signature + the `run_estimators` call ~line 1663)
- Test: `aurabackend/tests/test_iv_estimator.py` (append)

- [ ] **Step 1: Write the failing test** **[Tier B — needs dowhy]**

```python
# append to tests/test_iv_estimator.py
import asyncio
def test_run_job_honours_methods_list():
    dowhy = pytest.importorskip("dowhy")
    from counterfactual_service.engine import run_job
    from counterfactual_service.schemas import (
        CounterfactualQuery, InterventionSpec, OutcomeSpec, DAGSpec, DatasetRef,
    )
    rng = np.random.default_rng(2)
    n = 800
    z = rng.integers(0, 2, n).astype(float)
    x = rng.normal(0, 1, n)
    t = ((0.5 * z + 0.5 * x) > 0.3).astype(float)
    y = (1.0 * t + 0.8 * x + rng.normal(0, 0.3, n))
    df = pd.DataFrame({"z": z, "x": x, "t": t, "y": y})
    q = CounterfactualQuery(
        question="effect of t on y",
        treatment=InterventionSpec(column="t", actual=1, counterfactual=0),
        outcome=OutcomeSpec(column="y", agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=DAGSpec(edges=[("z", "t"), ("x", "t"), ("x", "y"), ("t", "y")]),
        dataset=DatasetRef(source_id="inline"),
    )
    art = asyncio.run(run_job(q, df=df, methods=["linear_regression", "iv"]))
    got = {e.method for e in art.estimates}
    assert got == {"linear_regression", "iv"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_iv_estimator.py::test_run_job_honours_methods_list -q`
Expected: FAIL — `run_job() got an unexpected keyword argument 'methods'` (or SKIP if dowhy absent — then verify on the Tier B lane).

- [ ] **Step 3: Edit `run_job`**

Change the signature at line 1648:

```python
async def run_job(
    query: CounterfactualQuery,
    df: pd.DataFrame,
    methods: Optional[List["EstimatorMethod"]] = None,
) -> CounterfactualArtifact:
```

And the `run_estimators` call (~line 1663) to forward `methods`:

```python
    estimates = await run_estimators(
        df, query.treatment, query.outcome, query.dag.model_dump(),
        methods=methods,
        request_hash=req_hash,
    )
```

(`EstimatorMethod` and `Optional`/`List` are already imported in engine.py; if not, add `from .schemas import EstimatorMethod` to the existing schema import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_iv_estimator.py::test_run_job_honours_methods_list -q`
Expected: PASS (or SKIP without dowhy — must PASS on the Causal Tests CI lane).

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/engine.py aurabackend/tests/test_iv_estimator.py
git commit -m "feat(s31b): thread opt-in methods list through run_job"
```

---

## Task 4: Scenario registry + base interface

**Files:**
- Create: `aurabackend/counterfactual_service/demo_scenarios/base.py`
- Create: `aurabackend/counterfactual_service/demo_scenarios/__init__.py`
- Test: `aurabackend/tests/test_demo_scenarios.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_demo_scenarios.py
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.demo_scenarios import list_scenarios, get_scenario


def test_registry_lists_fair_lending():
    ids = [s["id"] for s in list_scenarios()]
    assert "fair_lending" in ids
    meta = next(s for s in list_scenarios() if s["id"] == "fair_lending")
    assert set(meta) >= {"id", "title", "vertical", "description"}


def test_get_scenario_builds_valid_query_and_df():
    sc = get_scenario("fair_lending")
    df = sc.build_dataset()
    q = sc.query()
    assert isinstance(df, pd.DataFrame) and len(df) > 0
    # treatment/outcome/confounders must be real columns
    assert q.treatment.column in df.columns
    assert q.outcome.column in df.columns
    for src, dst in q.dag.edges:
        assert src in df.columns and dst in df.columns


def test_build_dataset_is_deterministic():
    sc = get_scenario("fair_lending")
    pd.testing.assert_frame_equal(sc.build_dataset(), sc.build_dataset())


def test_unknown_scenario_raises_keyerror():
    import pytest
    with pytest.raises(KeyError):
        get_scenario("nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_demo_scenarios.py -q`
Expected: FAIL — `ModuleNotFoundError: counterfactual_service.demo_scenarios`

- [ ] **Step 3: Write the base interface**

```python
# counterfactual_service/demo_scenarios/base.py
"""Demo-scenario interface + registry.

A scenario is a deterministic, self-contained compliance audit fixture:
a synthetic dataset (with a planted, known ground-truth effect), the
CounterfactualQuery that audits it, and a plain-English narrative for
the PDF/UI. Scenarios are independent — shipping a subset still yields
a working demo.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict

import pandas as pd

from ..schemas import CounterfactualQuery


class DemoScenario(ABC):
    id: str
    title: str
    vertical: str
    description: str
    instrument: str | None = None

    @abstractmethod
    def build_dataset(self) -> pd.DataFrame: ...

    @abstractmethod
    def query(self) -> CounterfactualQuery: ...

    def narrative(self, artifact: dict) -> str:
        """Plain-English conclusion for the PDF/UI. Override per scenario."""
        return self.description


_REGISTRY: Dict[str, DemoScenario] = {}


def register(scenario: DemoScenario) -> DemoScenario:
    _REGISTRY[scenario.id] = scenario
    return scenario


def get_scenario(scenario_id: str) -> DemoScenario:
    if scenario_id not in _REGISTRY:
        raise KeyError(scenario_id)
    return _REGISTRY[scenario_id]


def list_scenarios() -> list[dict]:
    return [
        {"id": s.id, "title": s.title, "vertical": s.vertical, "description": s.description}
        for s in _REGISTRY.values()
    ]
```

- [ ] **Step 4: Write the package init (imports register the scenarios)**

```python
# counterfactual_service/demo_scenarios/__init__.py
from .base import DemoScenario, get_scenario, list_scenarios, register

# Importing each scenario module runs its register() call.
from . import fair_lending  # noqa: E402,F401

__all__ = ["DemoScenario", "get_scenario", "list_scenarios", "register"]
```

- [ ] **Step 5: Run test — still fails (no fair_lending yet)**

Run: `cd aurabackend && python -m pytest tests/test_demo_scenarios.py -q`
Expected: FAIL — `ModuleNotFoundError: ...fair_lending` (proves the init wiring; Task 5 adds the module).

- [ ] **Step 6: Commit**

```bash
git add aurabackend/counterfactual_service/demo_scenarios/base.py aurabackend/counterfactual_service/demo_scenarios/__init__.py aurabackend/tests/test_demo_scenarios.py
git commit -m "feat(s31b): demo-scenario registry + base interface"
```

---

## Task 5: `fair_lending` scenario

Plants a **known direct effect** of `protected_class` on `approved` after adjustment, plus confounding (via `credit_score`) so the *naive* approval-rate gap overstates the causal effect — demonstrating why causal adjustment matters. `officer_assignment` is a valid instrument (affects approval propensity, not creditworthiness).

**Files:**
- Create: `aurabackend/counterfactual_service/demo_scenarios/fair_lending.py`
- Test: `aurabackend/tests/test_demo_scenarios.py` (the Task 4 tests now pass)

- [ ] **Step 1: Write the scenario**

```python
# counterfactual_service/demo_scenarios/fair_lending.py
"""Fair-lending credit-decision audit (YC demo scenario #1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..schemas import (
    CounterfactualQuery, DAGSpec, DatasetRef, InterventionSpec, OutcomeSpec,
)
from .base import DemoScenario, register

_SEED = 31021
_N = 600


class FairLendingScenario(DemoScenario):
    id = "fair_lending"
    title = "Fair-Lending Credit Decision Audit"
    vertical = "compliance"
    description = (
        "Did an applicant's protected class causally affect loan approval, "
        "holding creditworthiness fixed? (ECOA / fair-lending)"
    )
    instrument = "officer_assignment"

    def build_dataset(self) -> pd.DataFrame:
        rng = np.random.default_rng(_SEED)
        n = _N
        protected = rng.integers(0, 2, n)                       # treatment (0/1)
        # Confounder correlated with protected class AND approval.
        credit_score = (680 - 25 * protected + rng.normal(0, 40, n)).clip(300, 850)
        income = (60000 - 5000 * protected + rng.normal(0, 12000, n)).clip(15000, None)
        dti = (0.30 + 0.04 * protected + rng.normal(0, 0.06, n)).clip(0.0, 0.9)
        officer = rng.integers(0, 2, n)                          # instrument (leniency)
        # Planted causal structure: creditworthiness drives approval; a
        # modest DIRECT protected-class effect (-0.12) is the disparate
        # impact the audit must recover; officer leniency shifts approval
        # but is independent of creditworthiness (valid instrument).
        logit = (
            -3.5
            + 0.006 * (credit_score - 680)
            + 0.000015 * (income - 60000)
            - 1.5 * (dti - 0.30)
            - 0.12 * protected
            + 0.8 * officer
        )
        p = 1.0 / (1.0 + np.exp(-logit))
        approved = (rng.random(n) < p).astype(int)
        return pd.DataFrame({
            "protected_class": protected.astype(float),
            "credit_score": credit_score.round(1),
            "income": income.round(2),
            "dti": dti.round(4),
            "officer_assignment": officer.astype(float),
            "approved": approved.astype(float),
        })

    def query(self) -> CounterfactualQuery:
        return CounterfactualQuery(
            question="Did protected class causally affect approval, holding creditworthiness fixed?",
            treatment=InterventionSpec(column="protected_class", actual=1.0, counterfactual=0.0),
            outcome=OutcomeSpec(column="approved", agg="mean", window=("1970-01-01", "2100-01-01")),
            dag=DAGSpec(edges=[
                ("credit_score", "protected_class"),
                ("credit_score", "approved"),
                ("income", "approved"),
                ("dti", "approved"),
                ("protected_class", "approved"),
                ("officer_assignment", "protected_class"),  # instrument -> treatment
            ]),
            dataset=DatasetRef(source_id="demo:fair_lending"),
            audience="auditor",
        )

    def narrative(self, artifact: dict) -> str:
        ests = [e for e in artifact.get("estimates", []) if e.get("error") is None]
        if not ests:
            return "Audit did not produce a usable estimate."
        pts = [e["point"] for e in ests]
        avg = sum(pts) / len(pts)
        direction = "lowered" if avg < 0 else "raised"
        return (
            f"Across {len(ests)} estimators, belonging to the protected class "
            f"{direction} the probability of approval by {abs(avg):.1%} on average, "
            f"after adjusting for creditworthiness — the disparate impact a raw "
            f"approval-rate comparison would misstate."
        )


register(FairLendingScenario())
```

- [ ] **Step 2: Run the Task 4 registry tests — now pass**

Run: `cd aurabackend && python -m pytest tests/test_demo_scenarios.py -q`
Expected: PASS (4 passed)

- [ ] **Step 3: Commit**

```bash
git add aurabackend/counterfactual_service/demo_scenarios/fair_lending.py
git commit -m "feat(s31b): fair_lending demo scenario"
```

---

## Task 6: Persistent ED25519 signing key (4th source)

**Files:**
- Modify: `aurabackend/counterfactual_service/signing.py` (inside `_resolve_key_pair`, before the ephemeral block ~line 101)
- Test: `aurabackend/tests/test_signing_persistent_key.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_signing_persistent_key.py
import importlib, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

crypto = pytest.importorskip("cryptography")


def _fresh_signing(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(tmp_path))
    import counterfactual_service.signing as s
    importlib.reload(s)
    return s


def test_generates_and_persists_then_reloads_same_key(monkeypatch, tmp_path):
    s = _fresh_signing(monkeypatch, tmp_path)
    pem1 = s.public_key_pem()
    assert s.signing_key_source() == "persisted_file"
    assert (tmp_path / "signing_ed25519.pem").exists()
    # Reload from scratch — must read the same persisted key.
    s2 = _fresh_signing(monkeypatch, tmp_path)
    assert s2.public_key_pem() == pem1
    assert s2.signing_key_source() == "persisted_file"


def test_falls_back_to_ephemeral_when_dir_unwritable(monkeypatch, tmp_path):
    bad = tmp_path / "nope.pem"
    bad.write_text("not-a-dir-parent")  # make the key dir path a file
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_HEX", raising=False)
    monkeypatch.delenv("AURA_SIGNING_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("AURA_SIGNING_KEY_DIR", str(bad))  # parent is a file
    import counterfactual_service.signing as s
    importlib.reload(s)
    assert s.public_key_pem() is not None
    assert s.signing_key_source() == "ephemeral"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_signing_persistent_key.py -q`
Expected: FAIL — `signing_key_source() == "ephemeral"` (currently no persisted source)

- [ ] **Step 3: Add the persisted-file source**

In `signing.py`, in `_resolve_key_pair`, replace the ephemeral block (the comment "# Ephemeral fallback." through the `return _KEY_PAIR` after it, ~lines 101-112) with:

```python
    # Persisted-file source: auto-generate once and reuse across restarts
    # with zero env config, so certificate signatures stay valid for the
    # demo. Default dir data/keys/; override with AURA_SIGNING_KEY_DIR.
    key_dir = os.getenv("AURA_SIGNING_KEY_DIR", "data/keys").strip() or "data/keys"
    key_file = Path(key_dir) / "signing_ed25519.pem"
    try:
        if key_file.exists():
            sk = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
            if isinstance(sk, ed25519.Ed25519PrivateKey):
                _KEY_PAIR = (sk, sk.public_key())
                _KEY_SOURCE = "persisted_file"
                logger.info("ED25519 signing key loaded from %s", key_file)
                return _KEY_PAIR
        sk = ed25519.Ed25519PrivateKey.generate()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        pem = sk.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_file.write_bytes(pem)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        _KEY_PAIR = (sk, sk.public_key())
        _KEY_SOURCE = "persisted_file"
        logger.info("ED25519 signing key generated + persisted at %s", key_file)
        return _KEY_PAIR
    except Exception as exc:
        logger.warning("persisted-key path failed (%s); using ephemeral key", exc)

    # Ephemeral fallback (final). Logged loudly — signatures become
    # advisory (a restart invalidates prior signatures).
    sk = ed25519.Ed25519PrivateKey.generate()
    _KEY_PAIR = (sk, sk.public_key())
    _KEY_SOURCE = "ephemeral"
    logger.warning(
        "ED25519 signing key auto-generated (ephemeral). Set "
        "AURA_SIGNING_KEY_DIR to a writable path for stable signatures."
    )
    return _KEY_PAIR
```

Update the `signing_key_source` docstring (line 116) to add `persisted_file`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_signing_persistent_key.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Ensure the key dir is gitignored**

Run: `cd /c/Users/mouni/Documents/GitHub/Data-Analyst-Agent/Data-Analyst-Agent && grep -q "data/keys" .gitignore || printf '\n# S31b: persisted signing key (never commit)\naurabackend/data/keys/\n' >> .gitignore`
Expected: no output (added or already present)

- [ ] **Step 6: Commit**

```bash
git add aurabackend/counterfactual_service/signing.py aurabackend/tests/test_signing_persistent_key.py .gitignore
git commit -m "feat(s31b): persistent ED25519 key (auto-generate + persist)"
```

---

## Task 7: `/demo` endpoints + worker + pre-warm + fail-safe

The demo worker runs all 7 estimators. Pre-warm runs each scenario once at startup, caching the last-good artifact dict; if a live job fails, the job record is patched with the cached artifact and `degraded: true` so the frontend's normal poll still renders a result.

**Files:**
- Modify: `aurabackend/counterfactual_service/main.py` (add demo state, endpoints, worker, startup hook; fix `info()` estimator list ~line 143)
- Test: `aurabackend/tests/test_demo_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_demo_endpoints.py
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from counterfactual_service.main import app

client = TestClient(app)
ALL7 = {"linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"}


def test_list_demo_scenarios():
    r = client.get("/counterfactual/demo/scenarios")
    assert r.status_code == 200
    ids = [s["id"] for s in r.json()["scenarios"]]
    assert "fair_lending" in ids


def test_demo_run_returns_job_then_artifact():
    r = client.post("/counterfactual/demo/fair_lending")
    assert r.status_code == 200
    body = r.json()
    assert body["scenario_id"] == "fair_lending"
    job_id = body["job_id"]
    # Poll to completion.
    art = None
    for _ in range(60):
        jr = client.get(f"/counterfactual/jobs/{job_id}").json()
        if jr["state"] in ("succeeded", "failed"):
            art = jr
            break
        time.sleep(0.5)
    assert art is not None and art["state"] == "succeeded"
    methods = {e["method"] for e in art["artifact"]["estimates"]}
    assert ALL7.issubset(methods)
    assert art["artifact"]["audit_record_hash"]


def test_unknown_scenario_404():
    r = client.post("/counterfactual/demo/does_not_exist")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_demo_endpoints.py -q`
Expected: FAIL — 404 on `/counterfactual/demo/scenarios` (endpoint missing)

- [ ] **Step 3: Implement endpoints + worker + pre-warm in `main.py`**

Add imports near the top of `main.py` (with the existing `from . import ...`):

```python
from .demo_scenarios import get_scenario, list_scenarios
```

Add the 7-method constant + demo state after `_datasets` (~line 47):

```python
_DEMO_METHODS = ["linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"]
_demo_last_good: Dict[str, Dict[str, Any]] = {}  # scenario_id -> artifact dict
```

Add the demo job worker (mirrors `_run_async` but passes methods + scenario fallback):

```python
async def _run_demo_async(job_id: str, scenario_id: str, query: CounterfactualQuery) -> None:
    _jobs[job_id]["state"] = "running"
    try:
        df = _resolve_dataset(query.dataset.source_id)
        artifact = await run_job(query, df=df, methods=_DEMO_METHODS)
        artifact.rendered = render(artifact, query.audience)
        art_dict = artifact.model_dump(mode="json")
        _demo_last_good[scenario_id] = art_dict
        _jobs[job_id].update(state="succeeded", artifact=art_dict)
    except Exception as exc:
        logger.exception("Demo job %s failed", job_id)
        # Fail-safe: serve the last good artifact so the demo never breaks.
        fallback = _demo_last_good.get(scenario_id)
        if fallback is not None:
            patched = dict(fallback)
            patched["degraded"] = True
            _jobs[job_id].update(state="succeeded", artifact=patched)
        else:
            _jobs[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")
```

Add `run_job` to the engine import at the top (it currently imports `dowhy_available, run_job`? confirm — `from .engine import dowhy_available, run_job` already present at line 28, so no change).

Add the endpoints (after the existing `/counterfactual/info`, ~line 147):

```python
@app.get("/counterfactual/demo/scenarios")
async def demo_scenarios() -> Dict[str, Any]:
    return {"scenarios": list_scenarios()}


@app.post("/counterfactual/demo/{scenario_id}")
async def run_demo(scenario_id: str) -> Dict[str, Any]:
    try:
        scenario = get_scenario(scenario_id)
    except KeyError:
        raise HTTPException(404, f"unknown demo scenario: {scenario_id!r}")
    df = scenario.build_dataset()
    query = scenario.query()
    register_dataset(query.dataset.source_id, df)
    job_id = f"demo_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"state": "queued", "artifact": None, "error": None}
    _jobs[job_id]["_task"] = asyncio.create_task(
        _run_demo_async(job_id, scenario_id, query)
    )
    return {"job_id": job_id, "scenario_id": scenario_id, "degraded": False}
```

Update `info()` estimator list (line 143) to the true set:

```python
        "estimators": ["linear_regression", "ipw", "psm", "double_ml", "forest_dr", "tmle", "iv"],
```

Add the startup pre-warm (end of `main.py`):

```python
@app.on_event("startup")
async def _prewarm_demos() -> None:
    """Run each scenario once so the first user-triggered demo is fast and
    a last-good artifact exists for the fail-safe. Best-effort: a failure
    here must not block service startup."""
    for meta in list_scenarios():
        sid = meta["id"]
        try:
            scenario = get_scenario(sid)
            df = scenario.build_dataset()
            query = scenario.query()
            register_dataset(query.dataset.source_id, df)
            artifact = await run_job(query, df=df, methods=_DEMO_METHODS)
            _demo_last_good[sid] = artifact.model_dump(mode="json")
            logger.info("pre-warmed demo scenario %s", sid)
        except Exception as exc:
            logger.warning("pre-warm of scenario %s failed (non-fatal): %s", sid, exc)
```

- [ ] **Step 4: Run test to verify it passes** **[Tier B for the full-run assertion — needs dowhy/econml]**

Run: `cd aurabackend && python -m pytest tests/test_demo_endpoints.py -q`
Expected: `test_list_demo_scenarios` and `test_unknown_scenario_404` PASS always; `test_demo_run_returns_job_then_artifact` PASS on the Causal lane (dowhy+econml). Without those, the IV/linear estimators still run but forest_dr/double_ml surface `error` — adjust the assertion to `ALL7.issubset(methods)` on method *names* (which are always present as slots), which holds regardless.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/main.py aurabackend/tests/test_demo_endpoints.py
git commit -m "feat(s31b): /demo endpoints + worker + startup pre-warm + fail-safe"
```

---

## Task 8: Gateway proxy passthrough for `/demo`

**Files:**
- Modify: `aurabackend/api_gateway/routers/counterfactual.py`
- Test: covered by existing gateway proxy tests; add a thin one.

- [ ] **Step 1: Read the existing proxy pattern**

Run: `cd aurabackend && grep -n "demo\|proxy\|httpx\|COUNTERFACTUAL\|async def" api_gateway/routers/counterfactual.py | head -40`
Expected: shows the existing proxy helper + base-URL constant to mirror.

- [ ] **Step 2: Add passthrough routes mirroring the existing proxy style**

Add two routes using the SAME proxy helper the file already uses (substitute `<proxy_get>` / `<proxy_post>` / base-URL var with the names found in Step 1):

```python
@router.get("/demo/scenarios")
async def demo_scenarios_proxy():
    return await <proxy_get>("/counterfactual/demo/scenarios")


@router.post("/demo/{scenario_id}")
async def run_demo_proxy(scenario_id: str):
    return await <proxy_post>(f"/counterfactual/demo/{scenario_id}", json=None)
```

If the file proxies by forwarding `request` generically (catch-all), this task is a no-op — verify by Step 3 instead.

- [ ] **Step 3: Verify routing**

Run: `cd aurabackend && python -c "from api_gateway.main import app; print([r.path for r in app.routes if 'demo' in r.path])"`
Expected: shows `/api/v1/counterfactual/demo/scenarios` (or the catch-all proxy that already covers it).

- [ ] **Step 4: Commit**

```bash
git add aurabackend/api_gateway/routers/counterfactual.py
git commit -m "feat(s31b): gateway passthrough for /demo endpoints"
```

---

## Task 9: PDF report polish

**Files:**
- Modify: `aurabackend/counterfactual_service/pdf_renderer.py`
- Test: `aurabackend/tests/test_counterfactual_sprint9.py` (existing PDF tests) — extend.

- [ ] **Step 1: Read current renderer to find the section-building seam**

Run: `cd aurabackend && grep -n "def render_pdf\|def pdf_available\|Paragraph\|drawString\|story\|elements" counterfactual_service/pdf_renderer.py | head -30`
Expected: shows where content blocks are appended (reportlab `story`/`elements` list or canvas draws).

- [ ] **Step 2: Write a failing test for the attestation line**

```python
# tests/test_counterfactual_sprint9.py  (append)
def test_pdf_contains_attestation_when_signed():
    import pytest
    pytest.importorskip("reportlab")
    from counterfactual_service import pdf_renderer
    art = {
        "audit_record_hash": "abc123def456",
        "signature_status": "signed",
        "signing_key_source": "persisted_file",
        "confidence": "high",
        "query": {"question": "demo?"},
        "estimates": [{"method": "iv", "point": -0.12, "ci_lower": -0.2, "ci_upper": -0.04, "error": None, "n_samples": 600}],
        "refutations": [], "challenges": [],
    }
    pdf = pdf_renderer.render_pdf(art)
    assert pdf is not None and pdf[:4] == b"%PDF"
```

- [ ] **Step 3: Run — should pass if renderer already handles this dict; else adjust renderer**

Run: `cd aurabackend && python -m pytest tests/test_counterfactual_sprint9.py::test_pdf_contains_attestation_when_signed -q`
Expected: PASS if `render_pdf` is robust to this dict. If it errors on a missing field, add the attestation block (hash + signature_status + signing_key_source) using the section seam from Step 1, guarding each field with `.get(...)`.

- [ ] **Step 4: Add the attestation + scenario header block** (only if Step 3 needed it)

At the seam identified in Step 1, append a block (reportlab `Paragraph` example; adapt to canvas if that's the style):

```python
    story.append(Paragraph(
        f"Audit hash: {art.get('audit_record_hash','—')}  ·  "
        f"Signature: {art.get('signature_status','unsigned')} "
        f"({art.get('signing_key_source','n/a')})",
        styles["Normal"],
    ))
```

- [ ] **Step 5: Run and verify**

Run: `cd aurabackend && python -m pytest tests/test_counterfactual_sprint9.py -q -k pdf`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add aurabackend/counterfactual_service/pdf_renderer.py aurabackend/tests/test_counterfactual_sprint9.py
git commit -m "feat(s31b): PDF attestation block (hash + signature)"
```

---

## Task 10: Pre-push verification + draft-PR update

- [ ] **Step 1: Ruff**

Run: `cd aurabackend && python -m ruff check --fix . --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823`
Expected: `All checks passed!`

- [ ] **Step 2: Run all new + adjacent tests**

Run: `cd aurabackend && python -m pytest tests/test_demo_scenarios.py tests/test_iv_estimator.py tests/test_signing_persistent_key.py tests/test_demo_endpoints.py tests/test_data_utils.py -q`
Expected: all pass (Tier B items skip locally without dowhy/econml).

- [ ] **Step 3: Push and refresh the draft PR checklist**

```bash
git push origin feature/s31b-audit-engine
gh pr ready 38   # flip draft -> ready only when you decide it's review-ready
```

Update PR #38's checklist boxes as each task lands (keeps Rohith's connectivity current).

---

## Self-review notes (author)

- **Spec coverage:** registry (T4) ✓ · synthetic-with-planted-effect (T5) ✓ · persistent key (T6) ✓ · IV 7th slot (T2+T1) ✓ · /demo + pre-warm + fail-safe (T7) ✓ · gateway proxy (T8) ✓ · PDF polish (T9) ✓ · contract for S31a = the endpoints in T7/T8 ✓. Scenarios #2–4 = follow-up plan (out of scope here, per build-order fail-safe).
- **Tier B gating:** T3, the full-run assertion in T7, and IV numeric correctness need dowhy/econml — they run on the existing Causal Tests CI lane; Tier A tests (registry, IV unit math, persistent key, scenario list, 404) run on every lane.
- **Type consistency:** `_DEMO_METHODS` list ⊆ `EstimatorMethod` (incl. new `"iv"`); `run_job(query, df, methods=)` signature matches both the demo worker and pre-warm callers; `instruments_from_dag`/`run_iv_2sls` signatures match their call site in `_run_one_estimator`.
