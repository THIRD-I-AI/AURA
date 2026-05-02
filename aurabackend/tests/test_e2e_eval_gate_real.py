"""
True 8-Layer E2E Evaluation Gate — Real-LLM Edition
====================================================
Companion to ``test_e2e_eval_gate.py`` (which uses a mock LLM for fast
CI). This variant exercises the **real** model path end-to-end:

    upload 75-col CSV  →  schema_columns indexer  →  chat router
                       →  run_orchestrator (LangGraph, Step 1)
                       →  real Groq/Gemini call
                       →  ChatResponse

It's the only test in the suite that proves the production rails actually
hold under a real model: that the planner's BATS directive shrinks the
plan, that providers report ``finish_reason="stop"`` (not ``"length"``),
that real provider-reported tokens stay under the per-run cost ceiling,
and that the LangGraph state machine routes correctly under a real
non-deterministic LLM.

Skipped automatically when no provider key is configured so devs without
API keys can run pytest. CI wires GROQ_API_KEY as a secret in the
``eval-gate-real`` job — see .github/workflows/ci.yml.
"""
from __future__ import annotations

import csv
import json
import os
import random
import string
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.budget import BudgetTracker, reset_current_budget, set_current_budget
from shared.llm_cache import MAX_TOKENS_PER_REQUEST
from shared.llm_provider import register_llm_observer

# ── Tunables (env-overridable for model swaps) ────────────────────────

PER_RUN_TOKEN_CEILING = int(os.getenv("AURA_EVAL_GATE_RUN_TOKENS", "32000"))
LATENCY_SLA_SECONDS = float(os.getenv("AURA_EVAL_GATE_LATENCY_S", "60"))
SCHEMA_COLUMN_COUNT = int(os.getenv("AURA_EVAL_GATE_COLUMNS", "75"))
SCHEMA_ROW_COUNT = int(os.getenv("AURA_EVAL_GATE_ROWS", "200"))
TABLE_NAME = "wide_sales_real"
QUESTION = "What is the total revenue by region?"


def _has_real_provider() -> bool:
    """A real provider is configured iff at least one of the supported keys
    is present. Mirrors the auto-detection precedence in
    shared.llm_provider so we skip when get_llm() would also fail."""
    return any(os.getenv(k) for k in (
        "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
    ))


# ── Observer that records every LLM call for assertion ───────────────

class _CollectingObserver:
    """Appended to the LLM observer chain so every call lands in
    ``self.calls``. Reads are race-free because TestClient is synchronous
    (the FastAPI app drives a single asyncio loop in-process)."""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def on_call(self, ctx: Any) -> None:
        self.calls.append({
            "provider": ctx.provider,
            "model": ctx.model,
            "prompt_tokens": ctx.prompt_tokens,
            "completion_tokens": ctx.completion_tokens,
            "usage_source": ctx.usage_source,
            "finish_reason": ctx.finish_reason,
            "cached": ctx.cached,
        })


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def wide_csv():
    """Plant a 75-column CSV in data/uploads/. Same shape as the mock
    gate so a regression in either is comparable to the other."""
    uploads = Path(__file__).resolve().parent.parent / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    csv_path = uploads / f"{TABLE_NAME}.csv"

    rng = random.Random(42)
    base_cols = ["region", "product", "channel", "revenue", "units"]
    extra_cols = [f"dim_{i:02d}" for i in range(SCHEMA_COLUMN_COUNT - len(base_cols))]
    columns = base_cols + extra_cols

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for _ in range(SCHEMA_ROW_COUNT):
            row = [
                rng.choice(["North", "South", "East", "West"]),
                rng.choice(["WidgetA", "WidgetB", "WidgetC"]),
                rng.choice(["online", "retail", "wholesale"]),
                round(rng.uniform(100, 5000), 2),
                rng.randint(1, 100),
            ]
            row.extend("".join(rng.choices(string.ascii_lowercase, k=6)) for _ in extra_cols)
            w.writerow(row)
    yield csv_path
    csv_path.unlink(missing_ok=True)


@pytest.fixture()
def observer():
    """Register the collecting observer for the duration of one test.

    register_llm_observer mutates a module-global list — pop our entry
    on teardown so other tests don't see leaked observations.
    """
    from shared import llm_provider
    obs = _CollectingObserver()
    register_llm_observer(obs)
    yield obs
    try:
        llm_provider._OBSERVERS.remove(obs)
    except ValueError:
        pass


@pytest.fixture()
def budget():
    """Bind a BATS tracker for the request span, reset on teardown.

    The tracker debits real provider-reported tokens via the
    _BudgetObserver wired in Step 2, so its ``tokens_consumed`` is the
    same number the cost-ceiling assertion checks against.
    """
    tracker = BudgetTracker(
        session_id="eval-gate-real",
        token_budget=PER_RUN_TOKEN_CEILING,
        tool_call_budget=20,
        wall_seconds=LATENCY_SLA_SECONDS,
    )
    token = set_current_budget(tracker)
    try:
        yield tracker
    finally:
        reset_current_budget(token)


@pytest.fixture()
def client(wide_csv):
    """FastAPI TestClient with the real LLM provider chain — no mock."""
    # Audit log emits to /var/log/aura/audit by default (TRAIGA wiring).
    # Disable it in CI so we don't need that volume to exist.
    os.environ["AURA_AUDIT_ENABLED"] = "false"
    from fastapi.testclient import TestClient

    from api_gateway.main import app
    return TestClient(app)


# ── The real-LLM gate ────────────────────────────────────────────────

V1 = "/api/v1"


@pytest.mark.skipif(not _has_real_provider(), reason="No real LLM provider key configured")
class TestE2EEvaluationGateReal:
    """One real-LLM /chat run, eight assertion layers — collected
    together so a single CI failure shows every regression at once."""

    def test_eight_layer_gate_real(self, client, observer, budget):
        findings: List[str] = []

        # ── Run 1 (primary) ─────────────────────────────────────────
        t0 = time.perf_counter()
        resp = client.post(f"{V1}/chat", json={"message": QUESTION, "auto_execute": True})
        wall_seconds = time.perf_counter() - t0

        # ── Layer 1: schema ─────────────────────────────────────────
        try:
            assert resp.status_code == 200, f"chat returned {resp.status_code}: {resp.text[:300]}"
            body = resp.json()
            assert isinstance(body, dict), "response not a JSON object"
            assert "execution_result" in body or "message" in body, (
                "response missing expected ChatResponse fields"
            )
        except AssertionError as exc:
            findings.append(f"L1 schema: {exc}")
            # Without a body we can't run any other layer — bail loudly.
            pytest.fail("L1 schema failed — cannot continue:\n" + "\n".join(findings))

        # ── Layer 2: latency ────────────────────────────────────────
        try:
            assert wall_seconds < LATENCY_SLA_SECONDS, (
                f"chat took {wall_seconds:.2f}s, exceeds {LATENCY_SLA_SECONDS}s SLA"
            )
        except AssertionError as exc:
            findings.append(f"L2 latency: {exc}")

        # ── Layer 3: per-call token budget ──────────────────────────
        try:
            assert observer.calls, "no LLM calls captured — provider not exercised"
            biggest = max(c["prompt_tokens"] for c in observer.calls)
            assert biggest <= MAX_TOKENS_PER_REQUEST, (
                f"largest single prompt was {biggest} tokens, exceeds "
                f"AURA_MAX_TOKENS_PER_REQUEST ({MAX_TOKENS_PER_REQUEST}). "
                "The 75-col schema must be focused/trimmed before reaching the model."
            )
        except AssertionError as exc:
            findings.append(f"L3 token budget: {exc}")

        # ── Layer 4: determinism ────────────────────────────────────
        # Real LLMs aren't bit-exact. Relax the check to "rerun returns
        # rows from the same table" — drift in surface SQL across runs
        # is acceptable; an empty-result rerun is not.
        try:
            second = client.post(f"{V1}/chat", json={"message": QUESTION, "auto_execute": True})
            assert second.status_code == 200, "determinism rerun failed"
            second_body = second.json()
            second_rows = ((second_body.get("execution_result") or {}).get("data") or [])
            first_rows = ((body.get("execution_result") or {}).get("data") or [])
            assert bool(first_rows) == bool(second_rows), (
                f"reruns disagree on whether to return rows: "
                f"first={len(first_rows)} vs second={len(second_rows)}"
            )
        except AssertionError as exc:
            findings.append(f"L4 determinism: {exc}")

        # ── Layer 5: safety ─────────────────────────────────────────
        try:
            payload_text = json.dumps(body).lower()
            for forbidden in ("drop table", "drop database", "truncate ", "delete from"):
                assert forbidden not in payload_text, (
                    f"destructive token {forbidden!r} surfaced in response"
                )
        except AssertionError as exc:
            findings.append(f"L5 safety: {exc}")

        # ── Layer 6: semantic relevance ─────────────────────────────
        try:
            payload_text = json.dumps(body).lower()
            assert "region" in payload_text or "revenue" in payload_text, (
                "response references neither 'region' nor 'revenue' — answer drifted"
            )
        except AssertionError as exc:
            findings.append(f"L6 semantic: {exc}")

        # ── Layer 7: per-run cost (real tokens via BATS tracker) ───
        try:
            # BudgetObserver from Step 2 has been debiting REAL provider
            # tokens into the tracker. tokens_consumed is the canonical
            # number; assert it instead of summing the observer (which
            # double-counts on cache hits).
            assert budget.tokens_consumed <= PER_RUN_TOKEN_CEILING, (
                f"run consumed {budget.tokens_consumed} provider-reported tokens "
                f"across {len(observer.calls)} LLM calls, "
                f"exceeds AURA_EVAL_GATE_RUN_TOKENS ({PER_RUN_TOKEN_CEILING})"
            )
        except AssertionError as exc:
            findings.append(f"L7 cost: {exc}")

        # ── Layer 8: observability + finish_reason ──────────────────
        try:
            # 8a — at least one call must come from a real provider
            # (proves the test isn't silently using a mock or cached-only).
            real_calls = [c for c in observer.calls if c["usage_source"] == "provider_reported"]
            assert real_calls, (
                f"no provider_reported usage records — all {len(observer.calls)} "
                f"calls fell back to estimation. The real provider path was not exercised."
            )

            # 8b — every real call must have finished naturally (`stop`).
            # `length` means the model hit its max_tokens cap — a budget
            # blow-up that would cause the run to silently truncate.
            length_truncated = [
                c for c in real_calls if c["finish_reason"] == "length"
            ]
            assert not length_truncated, (
                f"{len(length_truncated)} LLM call(s) finished with finish_reason='length' "
                f"(hit max_tokens cap) — the model was truncated mid-response. "
                f"Examples: {length_truncated[:2]}"
            )

            # 8c — AGENT_DURATION metric initialised (sanity).
            from shared.observability import AGENT_DURATION
            assert hasattr(AGENT_DURATION, "labels"), "AGENT_DURATION metric missing"
        except AssertionError as exc:
            findings.append(f"L8 observability/finish: {exc}")

        if findings:
            # Surface the captured calls in the failure message so the
            # CI log carries the evidence inline (no separate artefact
            # to chase).
            evidence = json.dumps(observer.calls, indent=2, default=str)[:2000]
            pytest.fail(
                "Real-LLM 8-layer eval gate failed:\n"
                + "\n".join(f"  - {f}" for f in findings)
                + f"\n\nLLM call evidence ({len(observer.calls)} calls):\n{evidence}"
            )
