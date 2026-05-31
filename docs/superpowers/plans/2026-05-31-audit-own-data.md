# Audit Your Own Data — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user audit their own uploaded CSV by mapping columns (treatment/outcome/confounders, optional instrument) → a signed, causally-honest certificate, run out-of-process so the gateway never freezes.

**Architecture:** Pure helpers turn a column-mapping into a validated DataFrame + a canonical-backdoor `CounterfactualQuery`; a `ProcessPoolExecutor` runs the existing `run_job` fan-out in a child process; the gateway POST endpoint pre-validates cheaply and offloads. An honesty layer (identification statement + E-value headline + data-quality) is attached to the *result dict* after signing, so `/verify` is untouched.

**Tech Stack:** FastAPI, Pydantic v2, pandas, numpy, `concurrent.futures.ProcessPoolExecutor`, pytest. Builds on `counterfactual_service/{engine,main,schemas}.py`.

---

## File Structure

- Create `aurabackend/counterfactual_service/audit_mapping.py` — pure functions: `build_dag_from_mapping`, `validate_and_prepare` (+ `DataQuality`), `build_query_from_mapping`, `select_methods`, `identification_statement`, `sensitivity_headline`.
- Create `aurabackend/counterfactual_service/audit_worker.py` — `get_audit_pool()` + the picklable `run_audit_subprocess(payload)`.
- Modify `aurabackend/counterfactual_service/main.py` — `AuditRequest` model, `POST /counterfactual/audit`, `_run_audit_job_async` worker, cheap pre-validate helpers.
- Modify `aurabackend/api_gateway/routers/counterfactual.py` — gateway passthrough for `/audit`.
- Tests: `aurabackend/tests/test_audit_mapping.py` (Tier A), `aurabackend/tests/test_audit_endpoint.py` (Tier A + Tier B).

All Tier A unless marked **[Tier B]** (needs econml/dowhy).

---

## Task 1: `build_dag_from_mapping` — canonical backdoor DAG

**Files:**
- Create: `aurabackend/counterfactual_service/audit_mapping.py`
- Test: `aurabackend/tests/test_audit_mapping.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audit_mapping.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from counterfactual_service.audit_mapping import build_dag_from_mapping


def test_dag_backdoor_edges_no_instrument():
    dag = build_dag_from_mapping("flag", "approved", ["income", "dti"], None)
    edges = set(dag.edges)
    assert ("income", "flag") in edges and ("income", "approved") in edges
    assert ("dti", "flag") in edges and ("dti", "approved") in edges
    assert ("flag", "approved") in edges
    # no instrument edge
    assert not any(e[1] == "flag" and e[0] not in ("income", "dti") for e in edges)


def test_dag_includes_instrument_edge():
    dag = build_dag_from_mapping("flag", "approved", ["income"], "officer")
    assert ("officer", "flag") in set(dag.edges)
    assert ("officer", "approved") not in set(dag.edges)  # exclusion restriction


def test_dag_rejects_self_loop_when_confounder_equals_treatment():
    # a confounder that duplicates the treatment would create a self-loop
    with pytest.raises(Exception):
        build_dag_from_mapping("flag", "approved", ["flag"], None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q`
Expected: FAIL — `ModuleNotFoundError: counterfactual_service.audit_mapping`

- [ ] **Step 3: Write the implementation**

```python
# counterfactual_service/audit_mapping.py
"""Turn a user column-mapping into a validated DataFrame + a causally-honest
CounterfactualQuery. Pure functions — no I/O, no engine calls — so they're
trivially testable and safe to run in a child process."""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/audit_mapping.py aurabackend/tests/test_audit_mapping.py
git commit -m "feat(audit): build_dag_from_mapping (canonical backdoor DAG)"
```

---

## Task 2: `validate_and_prepare` + `DataQuality` — boundary hygiene on real CSVs

**Files:**
- Modify: `aurabackend/counterfactual_service/audit_mapping.py`
- Test: `aurabackend/tests/test_audit_mapping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_audit_mapping.py
import numpy as np
import pandas as pd

from counterfactual_service.audit_mapping import DataQuality, validate_and_prepare


def _mapping(instrument=None):
    return {"treatment": "t", "outcome": "y", "confounders": ["x"], "instrument": instrument}


def test_validate_missing_column_raises():
    df = pd.DataFrame({"t": [0, 1], "y": [1, 0]})  # no 'x'
    with pytest.raises(ValueError) as e:
        validate_and_prepare(df, _mapping())
    assert "x" in str(e.value)


def test_validate_drops_nan_rows_and_counts():
    rng = np.random.default_rng(0)
    n = 200
    df = pd.DataFrame({"t": rng.integers(0, 2, n), "y": rng.integers(0, 2, n),
                       "x": rng.normal(size=n)})
    df.loc[:9, "x"] = np.nan  # 10 missing
    clean, dq = validate_and_prepare(df, _mapping())
    assert dq.n_dropped == 10 and dq.n_clean == n - 10
    assert clean["x"].isna().sum() == 0


def test_validate_too_few_rows_raises():
    df = pd.DataFrame({"t": [0, 1, 0], "y": [1, 0, 1], "x": [0.1, 0.2, 0.3]})
    with pytest.raises(ValueError) as e:
        validate_and_prepare(df, _mapping())
    assert "rows" in str(e.value).lower()


def test_validate_binarises_continuous_treatment_and_flags():
    rng = np.random.default_rng(1)
    n = 200
    df = pd.DataFrame({"t": rng.normal(size=n), "y": rng.integers(0, 2, n),
                       "x": rng.normal(size=n)})
    clean, dq = validate_and_prepare(df, _mapping())
    assert set(clean["t"].unique()) <= {0.0, 1.0}
    assert dq.treatment_is_binary is False
    assert any("binaris" in w.lower() for w in dq.warnings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q -k validate`
Expected: FAIL — `ImportError: cannot import name 'validate_and_prepare'`

- [ ] **Step 3: Write the implementation (append to `audit_mapping.py`)**

```python
class DataQuality(BaseModel):
    n_input: int
    n_clean: int
    n_dropped: int
    treatment_is_binary: bool
    warnings: List[str] = []


def validate_and_prepare(df: pd.DataFrame, mapping: dict) -> Tuple[pd.DataFrame, DataQuality]:
    """Coerce mapped columns to numeric, drop rows with missing values, enforce a
    minimum sample size, and binarise a non-binary treatment at its median.
    Raises ValueError (→ a clear 400 / failed job) on unrecoverable problems."""
    treatment = mapping["treatment"]
    outcome = mapping["outcome"]
    confounders = list(mapping.get("confounders") or [])
    instrument = mapping.get("instrument")
    cols = [treatment, outcome, *confounders] + ([instrument] if instrument else [])

    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"columns not found in data: {missing}")

    warnings: List[str] = []
    work = df[cols].copy()
    for c in cols:
        coerced = pd.to_numeric(work[c], errors="coerce")
        if coerced.isna().sum() > work[c].isna().sum():
            warnings.append(f"column '{c}' had non-numeric values that were dropped")
        work[c] = coerced

    n_input = len(work)
    work = work.dropna()
    n_clean = len(work)
    n_dropped = n_input - n_clean
    if n_clean < AUDIT_MIN_ROWS:
        raise ValueError(
            f"only {n_clean} usable rows after cleaning; need >= {AUDIT_MIN_ROWS}"
        )

    uniq = sorted(work[treatment].unique())
    treatment_is_binary = len(uniq) == 2
    if set(uniq) <= {0.0, 1.0}:
        pass  # already 0/1
    elif treatment_is_binary:
        lo, hi = uniq[0], uniq[1]
        work[treatment] = (work[treatment] == hi).astype(float)
    else:
        median = float(work[treatment].median())
        work[treatment] = (work[treatment] >= median).astype(float)
        warnings.append(
            f"treatment '{treatment}' was continuous; binarised at its median ({median:.3g})"
        )

    dq = DataQuality(
        n_input=n_input, n_clean=n_clean, n_dropped=n_dropped,
        treatment_is_binary=treatment_is_binary, warnings=warnings,
    )
    return work.reset_index(drop=True), dq
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/audit_mapping.py aurabackend/tests/test_audit_mapping.py
git commit -m "feat(audit): validate_and_prepare + DataQuality (real-CSV hygiene)"
```

---

## Task 3: query builder, method selection, honesty text

**Files:**
- Modify: `aurabackend/counterfactual_service/audit_mapping.py`
- Test: `aurabackend/tests/test_audit_mapping.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_audit_mapping.py
from counterfactual_service.audit_mapping import (
    build_query_from_mapping,
    identification_statement,
    select_methods,
    sensitivity_headline,
)


def test_select_methods_iv_only_with_instrument():
    assert select_methods(None) == ["double_ml", "tmle"]
    assert select_methods("officer") == ["double_ml", "tmle", "iv"]


def test_build_query_from_mapping_shapes():
    df = pd.DataFrame({"t": [0.0, 1.0] * 60, "y": [1.0, 0.0] * 60, "x": [0.1] * 120})
    q = build_query_from_mapping(df, _mapping())
    assert q.treatment.column == "t" and q.treatment.actual == 1.0 and q.treatment.counterfactual == 0.0
    assert q.outcome.column == "y"
    assert ("x", "t") in set(q.dag.edges) and ("t", "y") in set(q.dag.edges)
    assert q.dataset.source_id == "uploaded_file:decisions.csv"


def test_identification_statement_mentions_confounders_and_iv():
    s = identification_statement(_mapping())
    assert "income" not in s and "x" in s  # lists the mapped confounders
    assert "no instrument" in s.lower()
    s2 = identification_statement(_mapping(instrument="officer"))
    assert "officer" in s2 and "exclusion" in s2.lower()


def test_sensitivity_headline_reads_evalue():
    art = {"estimates": [
        {"method": "tmle", "error": None, "sensitivity": {"e_value_point": 1.8}},
        {"method": "double_ml", "error": None, "sensitivity": {"e_value_point": 1.6}},
    ]}
    h = sensitivity_headline(art)
    assert "1.8" in h or "1.6" in h
    assert "confounder" in h.lower()


# the build_query test references a file name; thread it through the mapping
def _mapping(instrument=None):  # noqa: F811 — override to add uploaded_file
    return {"uploaded_file": "decisions.csv", "treatment": "t", "outcome": "y",
            "confounders": ["x"], "instrument": instrument}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q -k "select_methods or build_query or identification or sensitivity_headline"`
Expected: FAIL — `ImportError` on the new names

- [ ] **Step 3: Write the implementation (append to `audit_mapping.py`)**

```python
def select_methods(instrument: Optional[str]) -> List[str]:
    """Fast, modern, doubly-robust default. IV only when an instrument is mapped.
    The slow classical DoWhy bootstrap methods and forest_dr (broken on binary
    outcomes) are deliberately excluded."""
    methods = ["double_ml", "tmle"]
    if instrument:
        methods.append("iv")
    return methods


def build_query_from_mapping(clean_df: pd.DataFrame, mapping: dict) -> CounterfactualQuery:
    """Build a CounterfactualQuery from a cleaned df + mapping. Treatment is already
    0/1 after validate_and_prepare, so actual=1 / counterfactual=0."""
    treatment = mapping["treatment"]
    outcome = mapping["outcome"]
    confounders = list(mapping.get("confounders") or [])
    instrument = mapping.get("instrument")
    return CounterfactualQuery(
        question=f"Causal effect of '{treatment}' on '{outcome}' (user audit).",
        treatment=InterventionSpec(column=treatment, actual=1.0, counterfactual=0.0),
        outcome=OutcomeSpec(column=outcome, agg="mean", window=("1970-01-01", "2100-01-01")),
        dag=build_dag_from_mapping(treatment, outcome, confounders, instrument),
        dataset=DatasetRef(source_id=f"uploaded_file:{mapping['uploaded_file']}"),
        audience="auditor",
    )


def identification_statement(mapping: dict) -> str:
    conf = ", ".join(mapping.get("confounders") or []) or "(none specified)"
    s = (
        "This estimate is valid only under the assumption of no unmeasured "
        f"confounding beyond the adjusted variables: {conf}. "
    )
    if mapping.get("instrument"):
        s += (
            f"The instrument '{mapping['instrument']}' is additionally assumed to "
            "affect the treatment but the outcome only through the treatment "
            "(the exclusion restriction)."
        )
    else:
        s += (
            "No instrument was supplied, so this is a backdoor-adjustment estimate; "
            "judge its robustness by the sensitivity bound below."
        )
    return s


def sensitivity_headline(artifact: dict) -> str:
    """Plain-English E-value headline from the strongest available estimate."""
    ok = [e for e in artifact.get("estimates", [])
          if e.get("error") is None and isinstance(e.get("sensitivity"), dict)]
    if not ok:
        return "Sensitivity to unmeasured confounding was not available for this audit."
    evals = [e["sensitivity"].get("e_value_point") for e in ok
             if e["sensitivity"].get("e_value_point") is not None]
    if not evals:
        return "Sensitivity to unmeasured confounding was not available for this audit."
    e = max(evals)  # the most conservative (largest) E-value across methods
    return (
        f"Robustness: an unmeasured confounder would need an E-value of about {e:.2f} "
        "(on the risk-ratio scale, beyond the measured associations) to fully explain "
        "away this effect."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py -q`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/audit_mapping.py aurabackend/tests/test_audit_mapping.py
git commit -m "feat(audit): query builder + method selection + honesty text"
```

---

## Task 4: `audit_worker.py` — the picklable subprocess entry + pool

**Files:**
- Create: `aurabackend/counterfactual_service/audit_worker.py`
- Test: `aurabackend/tests/test_audit_endpoint.py`

- [ ] **Step 1: Write the failing test** **[Tier B — needs econml]**

```python
# tests/test_audit_endpoint.py
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_demo_like_csv(path):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(7)
    n = 600
    x = rng.normal(0, 1, n)
    t = ((0.6 * x + rng.normal(0, 0.5, n)) > 0).astype(int)
    y = (0.5 + 0.4 * x - 0.6 * t + rng.normal(0, 0.3, n))
    pd.DataFrame({"flag": t, "approved": y, "score": x}).to_csv(path, index=False)


def test_run_audit_subprocess_produces_signed_artifact_with_honesty(tmp_path, monkeypatch):
    pytest.importorskip("econml")
    monkeypatch.chdir(tmp_path)
    up = tmp_path / "data" / "uploads"
    up.mkdir(parents=True)
    _write_demo_like_csv(up / "decisions.csv")

    from counterfactual_service.audit_worker import run_audit_subprocess
    result = run_audit_subprocess({
        "uploaded_file": "decisions.csv", "treatment": "flag",
        "outcome": "approved", "confounders": ["score"], "instrument": None,
    })
    methods = {e["method"] for e in result["estimates"]}
    assert {"double_ml", "tmle"}.issubset(methods)
    assert result["audit_record_hash"]
    assert result["signature_status"] == "signed"
    assert "identification" in result and "no unmeasured confounding" in result["identification"]
    assert "sensitivity_headline" in result
    assert result["data_quality"]["n_clean"] >= 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q`
Expected: FAIL — `ModuleNotFoundError: counterfactual_service.audit_worker`

- [ ] **Step 3: Write the implementation**

```python
# counterfactual_service/audit_worker.py
"""Out-of-process audit execution. The GIL-bound dowhy/econml fan-out runs here,
in a child process, so the gateway's event loop stays responsive.

``run_audit_subprocess`` is a top-level, picklable function (Windows spawn-safe)
and is ALSO directly callable in tests without the pool."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, Optional

_POOL: Optional[ProcessPoolExecutor] = None


def get_audit_pool() -> Optional[ProcessPoolExecutor]:
    """Lazily create the process pool. Tests monkeypatch this to return None so
    the endpoint falls back to the default thread executor (no spawn flakiness)."""
    global _POOL
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=int(os.getenv("AUDIT_POOL_WORKERS", "2")))
    return _POOL


def run_audit_subprocess(payload: Dict[str, Any]) -> Dict[str, Any]:
    """resolve → clean → build query → run_job → attach honesty layer. Returns the
    signed artifact dict plus identification / sensitivity_headline / data_quality."""
    from .audit_mapping import (
        build_query_from_mapping,
        identification_statement,
        select_methods,
        sensitivity_headline,
        validate_and_prepare,
    )
    from .engine import run_job
    from .main import _resolve_dataset
    from .renderers import render

    df = _resolve_dataset(f"uploaded_file:{payload['uploaded_file']}")
    clean_df, dq = validate_and_prepare(df, payload)
    query = build_query_from_mapping(clean_df, payload)
    methods = select_methods(payload.get("instrument"))

    artifact = asyncio.run(run_job(query, df=clean_df, methods=methods))
    artifact.rendered = render(artifact, query.audience)

    result = artifact.model_dump(mode="json")
    result["identification"] = identification_statement(payload)
    result["sensitivity_headline"] = sensitivity_headline(result)
    result["data_quality"] = dq.model_dump()
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q -k subprocess`
Expected: PASS (or SKIP without econml — must pass on the Causal lane)

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/audit_worker.py aurabackend/tests/test_audit_endpoint.py
git commit -m "feat(audit): out-of-process audit worker + honesty layer"
```

---

## Task 5: `POST /counterfactual/audit` endpoint + non-blocking job

**Files:**
- Modify: `aurabackend/counterfactual_service/main.py`
- Test: `aurabackend/tests/test_audit_endpoint.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_audit_endpoint.py
from fastapi.testclient import TestClient


def _client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    from counterfactual_service.main import app
    return TestClient(app)


def test_audit_404_when_file_missing(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "nope.csv", "treatment": "t", "outcome": "y", "confounders": []})
    assert r.status_code == 404


def test_audit_400_when_column_missing(tmp_path, monkeypatch):
    import pandas as pd
    c = _client(tmp_path, monkeypatch)
    pd.DataFrame({"flag": [0, 1], "approved": [1, 0]}).to_csv(
        tmp_path / "data" / "uploads" / "d.csv", index=False)
    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "d.csv", "treatment": "flag", "outcome": "approved",
        "confounders": ["does_not_exist"]})
    assert r.status_code == 400 and "does_not_exist" in r.json()["detail"]


def test_audit_returns_job_id_and_runs(tmp_path, monkeypatch):
    pytest.importorskip("econml")
    import time
    c = _client(tmp_path, monkeypatch)
    _write_demo_like_csv(tmp_path / "data" / "uploads" / "d.csv")
    # Run inline via the default thread executor (no ProcessPool spawn in tests).
    import counterfactual_service.audit_worker as worker
    monkeypatch.setattr(worker, "get_audit_pool", lambda: None)
    from counterfactual_service import main as m
    monkeypatch.setattr(m, "get_audit_pool", lambda: None)

    r = c.post("/counterfactual/audit", json={
        "uploaded_file": "d.csv", "treatment": "flag", "outcome": "approved",
        "confounders": ["score"]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    art = None
    for _ in range(120):
        jr = c.get(f"/counterfactual/jobs/{job_id}").json()
        if jr["state"] in ("succeeded", "failed"):
            art = jr
            break
        time.sleep(1)
    assert art is not None and art["state"] == "succeeded", art
    assert art["artifact"]["sensitivity_headline"]
    assert art["artifact"]["data_quality"]["n_clean"] >= 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q -k "audit_404 or audit_400"`
Expected: FAIL — 404/400 routes don't exist (currently 404 "Not Found" for the route itself, or 422)

- [ ] **Step 3: Implement the endpoint in `main.py`**

Add imports near the existing `from .demo_scenarios import ...`:

```python
import pathlib as _pathlib  # if not already imported (it is, as `pathlib`)
from .audit_worker import get_audit_pool, run_audit_subprocess
```

(`pathlib`, `asyncio`, `uuid`, `List`, `Optional`, `BaseModel`, `HTTPException` are already imported in main.py.)

Add the request model + helpers + endpoint after the `/demo` endpoints:

```python
class AuditRequest(BaseModel):
    uploaded_file: str
    treatment: str
    outcome: str
    confounders: List[str] = []
    instrument: Optional[str] = None


def _find_upload(name: str) -> Optional[pathlib.Path]:
    for d in (pathlib.Path("data/uploads"), pathlib.Path("api_gateway/uploads"),
              pathlib.Path("uploads")):
        p = d / name
        if p.exists():
            return p
    return None


def _csv_header_columns(path: pathlib.Path) -> list:
    import pandas as pd
    return list(pd.read_csv(path, nrows=0).columns)


async def _run_audit_job_async(job_id: str, payload: Dict[str, Any]) -> None:
    _jobs[job_id]["state"] = "running"
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(get_audit_pool(), run_audit_subprocess, payload)
        _jobs[job_id].update(state="succeeded", artifact=result)
    except Exception as exc:
        logger.exception("Audit job %s failed", job_id)
        _jobs[job_id].update(state="failed", error=f"{type(exc).__name__}: {exc}")


@app.post("/counterfactual/audit")
async def run_audit(req: AuditRequest) -> Dict[str, Any]:
    """Audit the user's own uploaded data. Cheap pre-validation here; the heavy,
    GIL-bound fan-out runs out-of-process so the gateway never blocks."""
    path = _find_upload(req.uploaded_file)
    if path is None:
        raise HTTPException(404, f"uploaded file not found: {req.uploaded_file!r}")
    if path.suffix.lower() == ".csv":
        header = _csv_header_columns(path)
        needed = [req.treatment, req.outcome, *req.confounders] + (
            [req.instrument] if req.instrument else [])
        missing = [c for c in needed if c not in header]
        if missing:
            raise HTTPException(400, f"columns not in file {req.uploaded_file!r}: {missing}")

    job_id = f"audit_{uuid.uuid4().hex[:12]}"
    _jobs[job_id] = {"state": "queued", "artifact": None, "error": None}
    _jobs[job_id]["_task"] = asyncio.create_task(
        _run_audit_job_async(job_id, req.model_dump())
    )
    return {"job_id": job_id}
```

Note: `run_in_executor(None, fn)` (when `get_audit_pool()` returns `None` in tests) uses the default thread executor — fine for correctness tests.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q`
Expected: `audit_404` + `audit_400` PASS always; `audit_returns_job_id_and_runs` PASS on the econml lane.

- [ ] **Step 5: Commit**

```bash
git add aurabackend/counterfactual_service/main.py aurabackend/tests/test_audit_endpoint.py
git commit -m "feat(audit): POST /counterfactual/audit (pre-validate + pool offload)"
```

---

## Task 6: Gateway passthrough for `/audit`

**Files:**
- Modify: `aurabackend/api_gateway/routers/counterfactual.py`
- Test: `aurabackend/tests/test_audit_endpoint.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_audit_endpoint.py
def test_audit_reachable_through_gateway(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    from api_gateway.main import app as gw
    gc = TestClient(gw)
    r = gc.post("/api/v1/counterfactual/audit", json={
        "uploaded_file": "nope.csv", "treatment": "t", "outcome": "y", "confounders": []})
    assert r.status_code == 404  # routed through, file-missing check fired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q -k gateway`
Expected: FAIL — route returns 404 "Not Found" for the *path* (not the file check) because the proxy route doesn't exist yet. (Verify the detail to distinguish.)

- [ ] **Step 3: Add the proxy route**

In `api_gateway/routers/counterfactual.py`, add the import (alongside the other `from counterfactual_service.main import (...)`):

```python
from counterfactual_service.main import (
    run_audit as _svc_run_audit,
)
from counterfactual_service.main import (
    AuditRequest,
)
```

And the route (after the `/demo` proxy routes):

```python
@router.post("/audit")
async def run_audit(req: AuditRequest):
    return await _svc_run_audit(req)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd aurabackend && python -m pytest tests/test_audit_endpoint.py -q -k gateway`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aurabackend/api_gateway/routers/counterfactual.py aurabackend/tests/test_audit_endpoint.py
git commit -m "feat(audit): gateway passthrough for /counterfactual/audit"
```

---

## Task 7: Verify, gitignore, push, PR

- [ ] **Step 1: Ruff**

Run: `cd aurabackend && python -m ruff check --fix counterfactual_service/audit_mapping.py counterfactual_service/audit_worker.py counterfactual_service/main.py api_gateway/routers/counterfactual.py tests/test_audit_mapping.py tests/test_audit_endpoint.py --ignore E501,E402,F401,W191,W291,W293,F841,E701,E712,F823`
Expected: `All checks passed!`

- [ ] **Step 2: Run the full new suite + a counterfactual regression sample**

Run: `cd aurabackend && python -m pytest tests/test_audit_mapping.py tests/test_audit_endpoint.py tests/test_demo_endpoints.py -q`
Expected: all pass (Tier B items skip without econml/dowhy).

- [ ] **Step 3: Manual smoke — prove the gateway does NOT freeze during a live audit**

Run (two terminals or background): boot `uvicorn api_gateway.main:app --port 8000`; upload a CSV via `POST /api/v1/files/upload`; `POST /api/v1/counterfactual/audit`; while it runs, confirm `GET /health` still returns 200 in < 1s. Expected: health stays responsive (audit is in a child process).

- [ ] **Step 4: Push + PR**

```bash
git push -u origin feature/audit-own-data
gh pr create --title "Audit your own data (causally-honest, out-of-process)" --body-file <pr-body>
```

PR body must include: the honesty-layer contract (identification + sensitivity_headline + data_quality fields on the artifact), the coordination note that Rohith's `AuditWizard` needs a file-picker + optional instrument field, and the smoke-test evidence that `/health` stays responsive during an audit.

---

## Self-review notes (author)

- **Spec coverage:** build_dag (T1) ✓ · validate_and_prepare + DataQuality (T2) ✓ · query builder + select_methods + honesty text (T3) ✓ · ProcessPool out-of-process worker (T4) ✓ · POST /audit pre-validate + offload (T5) ✓ · gateway proxy (T6) ✓ · honesty layer attached post-signing (T3/T4) ✓ · Tier A/B split called out per task ✓.
- **Type consistency:** `mapping` dict keys (`uploaded_file/treatment/outcome/confounders/instrument`) are identical across `validate_and_prepare`, `build_query_from_mapping`, `select_methods`, `identification_statement`, and `run_audit_subprocess`. `AuditRequest.model_dump()` produces exactly those keys. `select_methods` returns `["double_ml","tmle"(,"iv")]` matching §3. `get_audit_pool` monkeypatched in both `audit_worker` and `main` for the inline test.
- **No placeholders:** every step has runnable code + exact commands.
- **Known Tier-B gating:** T4 + the full-run tests in T5 need econml/dowhy → Causal lane; all pure-helper + endpoint-validation tests are Tier A.
