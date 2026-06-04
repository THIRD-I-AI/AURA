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
    # `eff` carries the auto-encoded column names (one-hot dummies, etc.) so the
    # DAG + identification statement adjust on the columns actually used.
    clean_df, dq, eff = validate_and_prepare(df, payload)
    query = build_query_from_mapping(clean_df, eff)
    methods = select_methods(eff.get("instrument"))

    # Bound the LLM adversarial critic so a numeric audit never blocks on a
    # slow/rate-limited provider (the COMPAS run hung here). Deterministic
    # checks still run; the skip is surfaced in the artifact warnings.
    critic_timeout = float(os.getenv("AURA_AUDIT_CRITIC_TIMEOUT_S", "8"))
    artifact = asyncio.run(run_job(query, df=clean_df, methods=methods,
                                   critic_timeout=critic_timeout))
    artifact.rendered = render(artifact, query.audience)

    result = artifact.model_dump(mode="json")
    result["identification"] = identification_statement(eff)
    result["sensitivity_headline"] = sensitivity_headline(result)
    result["data_quality"] = dq.model_dump()
    return result
